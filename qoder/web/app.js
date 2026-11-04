const USER = new URLSearchParams(location.search).get("user") || "demo-user";
const $ = (id) => document.getElementById(id);

const state = {
  bootstrap: null,
  profile: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!response.ok) {
    const detail = data && data.detail ? data.detail : text;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

// ---------------------------------------------------------------- health + bootstrap
async function loadHealth() {
  try {
    const health = await api("/api/health");
    const seekdb = $("status-seekdb");
    seekdb.textContent = health.seekdb.ok ? `seekdb ${health.seekdb.version}` : "seekdb 不可用";
    seekdb.className = "chip " + (health.seekdb.ok ? "ok" : "bad");
    const pc = $("status-pc");
    pc.textContent = health.powercontext.ok ? "PowerContext ready" : "PowerContext 不可用";
    pc.className = "chip " + (health.powercontext.ok ? "ok" : "bad");
    $("status-embed").textContent = `embedding ${health.embedding.provider}`;
  } catch (error) {
    $("status-seekdb").className = "chip bad";
    $("status-seekdb").textContent = "连接失败";
  }
}

async function loadBootstrap() {
  const data = await api(`/api/bootstrap?user=${encodeURIComponent(USER)}`);
  state.bootstrap = data;
  state.profile = data.memory.profile;

  const examples = $("examples");
  examples.innerHTML = "";
  for (const question of data.examples) {
    examples.append(el("button", {
      type: "button",
      onclick: () => { $("question").value = question; $("recall-form").requestSubmit(); },
    }, question));
  }

  const courseChecks = $("course-checks");
  courseChecks.innerHTML = "";
  for (const course of data.courses) {
    const input = el("input", { type: "checkbox", value: course });
    input.checked = data.memory.profile.courses.includes(course);
    courseChecks.append(el("label", {}, input, course));
  }

  const typeChecks = $("type-checks");
  typeChecks.innerHTML = "";
  for (const type of data.source_types) {
    const input = el("input", { type: "checkbox", value: type });
    input.checked = data.memory.profile.source_types.includes(type);
    typeChecks.append(el("label", {}, input, type));
  }

  $("tag").value = data.memory.profile.tag || "";
  $("kw").value = data.memory.profile.keyword_weight;
  $("vw").value = data.memory.profile.vector_weight;
  $("topk").value = data.memory.profile.top_k;
  $("focus").value = data.memory.focus || "";
  syncOutputs();
  renderProfileState(data.memory);

  const reviewCourse = $("review-course");
  reviewCourse.innerHTML = "";
  reviewCourse.append(el("option", { value: "" }, "全部课程"));
  for (const course of data.courses) reviewCourse.append(el("option", { value: course }, course));
  if (data.memory.focus) {
    const match = data.courses.find((course) => data.memory.focus.includes(course));
    if (match) reviewCourse.value = match;
  }

  await loadReview();
  await loadEvents();
}

function syncOutputs() {
  $("kw-out").textContent = Number($("kw").value).toFixed(2);
  $("vw-out").textContent = Number($("vw").value).toFixed(2);
  $("topk-out").textContent = $("topk").value;
}

function renderProfileState(memory) {
  const parts = [];
  parts.push(`Scope：${memory.scope_id || "（未创建）"}`);
  parts.push(`检索偏好来源：${memory.profile_source === "powercontext" ? "PowerContext 记忆" : "默认值"}`);
  if (memory.focus) parts.push(`当前聚焦：${memory.focus}`);
  if (memory.learning && memory.learning.weak && memory.learning.weak.length) {
    parts.push(`薄弱点（已记住 ${memory.learning.weak.length} 个）：${memory.learning.weak.join("、")}`);
  }
  if (memory.last_session) parts.push(`上次会话：${memory.last_session}`);
  $("profile-state").textContent = parts.join("\n");
}

// ---------------------------------------------------------------- recall
$("recall-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("question").value.trim();
  if (!question) return;
  const button = event.submitter || event.target.querySelector("button");
  button.disabled = true;
  button.textContent = "检索中…";
  try {
    const data = await api("/api/recall", {
      method: "POST",
      body: JSON.stringify({ user: USER, question }),
    });
    renderRecall(data);
  } catch (error) {
    alert("检索失败：" + error.message);
  } finally {
    button.disabled = false;
    button.textContent = "检索";
    loadEvents();
  }
});

function renderRecall(data) {
  $("recall-result").classList.remove("hidden");

  const filters = $("filter-view");
  filters.innerHTML = "";
  const chips = [];
  const profile = data.profile;
  chips.push(`课程：${profile.courses.length ? profile.courses.join("、") : "全部"}`);
  chips.push(`类型：${profile.source_types.length ? profile.source_types.join("、") : "全部"}`);
  if (profile.tag) chips.push(`标签：${profile.tag}`);
  chips.push(`权重 关键词 ${profile.keyword_weight} / 向量 ${profile.vector_weight}`);
  chips.push(`Top ${profile.top_k}`);
  chips.push(`全文词：${data.keywords.slice(0, 6).join(" ")}`);
  for (const chip of chips) filters.append(el("span", {}, chip));

  $("recall-elapsed").textContent = `seekdb ${data.elapsed_ms} ms`;
  $("recall-count").textContent = `${data.hits.length} 条命中`;

  const hits = $("hits");
  hits.innerHTML = "";
  if (!data.hits.length) {
    hits.append(el("li", { class: "hit" }, "没有命中。试试放宽课程/标签过滤，或换个问法。"));
  }
  for (const hit of data.hits) {
    const fts = hit.fts_score === null ? 0 : Math.min(1, hit.fts_score / 2);
    const vec = hit.vec_score === null ? 0 : Math.max(0, Math.min(1, hit.vec_score));
    hits.append(el("li", { class: "hit" },
      el("div", { class: "top" },
        el("div", {},
          el("div", { class: "title" }, hit.title),
          el("div", { class: "meta" },
            `${hit.course || "未分类"} · ${hit.topic || "—"} · ${hit.source_type}`,
            ...hit.tags.map((tag) => el("span", { class: "tag" }, tag)),
          ),
        ),
        el("div", { class: "score" }, hit.score.toFixed(5)),
      ),
      el("div", { class: "excerpt" }, hit.excerpt + "…"),
      el("div", { class: "bars" },
        el("div", { class: "bar" }, "全文 ",
          el("i", { style: `width:${Math.round(fts * 90) + 4}px` }), " ",
          hit.fts_score === null ? "—" : hit.fts_score.toFixed(3)),
        el("div", { class: "bar vec" }, "向量 ",
          el("i", { style: `width:${Math.round(vec * 90) + 4}px` }), " ",
          hit.vec_score === null ? "—" : hit.vec_score.toFixed(3)),
      ),
    ));
  }

  const chain = $("memory-events");
  chain.innerHTML = "";
  for (const event of data.memory_events) {
    const isPc = event.system === "PowerContext";
    let detail;
    if (event.error) {
      detail = el("span", {}, "失败：" + event.error);
    } else if (event.result) {
      detail = el("span", { html: `<code>${escapeHtml(event.api)}</code> → ${escapeHtml(JSON.stringify(event.result))}` });
    } else if (event.hits) {
      detail = el("span", {}, `命中 ${event.hits.length} 条：${event.hits.join("、")}（${event.elapsed_ms} ms）`);
    } else if (event.artifact_id) {
      detail = el("span", { html: `<code>${escapeHtml(event.api)}</code> → artifact <code>${escapeHtml(event.artifact_id)}</code>` });
    } else {
      detail = el("span", {}, escapeHtml(JSON.stringify(event)));
    }
    chain.append(el("li", { class: isPc ? "pc" : "" },
      el("span", { class: "sys" }, `${event.system}\n${event.signal}`),
      detail,
    ));
  }
  $("sql-view").textContent = data.sql;
  $("sql-view").classList.toggle("hidden", !$("show-sql").checked);
  $("sql-view")._fullSql = data.sql;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

$("show-sql").addEventListener("change", (event) => {
  const pre = $("sql-view");
  pre.classList.toggle("hidden", !event.target.checked);
  if (!event.target.checked) pre.textContent = "";
  else if (pre._fullSql) pre.textContent = pre._fullSql;
});

// ---------------------------------------------------------------- profile
$("kw").addEventListener("input", () => {
  $("vw").value = (1 - Number($("kw").value)).toFixed(2);
  syncOutputs();
});
$("vw").addEventListener("input", () => {
  $("kw").value = (1 - Number($("vw").value)).toFixed(2);
  syncOutputs();
});
$("topk").addEventListener("input", syncOutputs);

function collectProfile(reason) {
  const courses = [...$("course-checks").querySelectorAll("input:checked")].map((i) => i.value);
  const sourceTypes = [...$("type-checks").querySelectorAll("input:checked")].map((i) => i.value);
  return {
    user: USER,
    courses,
    source_types: sourceTypes,
    keyword_weight: Number($("kw").value),
    vector_weight: Number($("vw").value),
    top_k: Number($("topk").value),
    tag: $("tag").value.trim(),
    reason,
  };
}

$("profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify(collectProfile("用户在界面调整检索偏好")),
    });
    renderProfileState(data.snapshot);
    $("profile-state").textContent += "\n✓ 已写入 PowerContext";
    loadEvents();
  } catch (error) {
    alert("保存失败：" + error.message);
  }
});

$("focus-btn").addEventListener("click", async () => {
  const focus = $("focus").value.trim();
  if (!focus) return;
  try {
    await api("/api/focus", { method: "POST", body: JSON.stringify({ user: USER, focus }) });
    const data = await api(`/api/profile?user=${encodeURIComponent(USER)}`);
    renderProfileState(data);
    await loadReview();
    loadEvents();
  } catch (error) {
    alert("更新失败：" + error.message);
  }
});

// ---------------------------------------------------------------- review
$("review-btn").addEventListener("click", loadReview);
$("review-course").addEventListener("change", loadReview);

async function loadReview() {
  const course = $("review-course").value;
  try {
    const data = await api(`/api/review?user=${encodeURIComponent(USER)}&course=${encodeURIComponent(course)}`);
    const meta = [];
    meta.push(`本期 ${data.plan.length} 题 · 待复习 ${data.due_count}/${data.card_count}`);
    if (data.weak_points.length) meta.push(`PowerContext 记住的薄弱点：${data.weak_points.join("、")}`);
    if (data.last_session) meta.push(`上次会话：${data.last_session}`);
    $("review-meta").textContent = meta.join("\n");

    const cards = $("cards");
    cards.innerHTML = "";
    if (!data.plan.length) cards.append(el("div", { class: "hint small" }, "这个范围内暂时没有待复习的题目。"));
    for (const item of data.plan) {
      const meta = el("div", { class: "meta" },
        el("span", {}, item.course || "未分类"),
        el("span", {}, item.topic || "—"),
        item.overdue ? el("span", { class: "weak" }, "已到期") : el("span", {}, "未到期"),
        item.from_weak_point ? el("span", { class: "weak" }, "薄弱点优先") : null,
      );
      const answer = el("div", { class: "a hidden" }, item.answer);
      const card = el("div", { class: "card" },
        el("div", { class: "q" }, item.question),
        el("button", {
          class: "ghost small",
          type: "button",
          onclick: () => answer.classList.toggle("hidden"),
        }, "看答案"),
        answer,
        meta,
        el("div", { class: "grades" },
          ...[["忘了", 0, "0"], ["勉强", 3, "3"], ["记得", 4, "4"], ["很熟", 5, "5"]].map(([label, grade, value]) =>
            el("button", {
              class: "ghost", type: "button", "data-grade": value,
              onclick: () => gradeCard(item.card_id, grade),
            }, label)),
        ),
      );
      cards.append(card);
    }
  } catch (error) {
    $("review-meta").textContent = "加载失败：" + error.message;
  }
}

async function gradeCard(cardId, grade) {
  try {
    const data = await api("/api/review/grade", {
      method: "POST",
      body: JSON.stringify({ user: USER, card_id: cardId, grade }),
    });
    const memory = data.memory && data.memory.ok
      ? `，PowerContext 已记录薄弱点：${(data.memory.weak || []).join("、") || "无"}`
      : "，PowerContext 写入失败";
    alert(`下次复习：${data.next_review_at}（间隔 ${data.interval_days} 天）${memory}`);
    await loadReview();
    const profile = await api(`/api/profile?user=${encodeURIComponent(USER)}`);
    renderProfileState(profile);
    loadEvents();
  } catch (error) {
    alert("评分失败：" + error.message);
  }
}

// ---------------------------------------------------------------- events
async function loadEvents() {
  try {
    const data = await api(`/api/events?user=${encodeURIComponent(USER)}&limit=25`);
    const tbody = $("events-table").querySelector("tbody");
    tbody.innerHTML = "";
    for (const event of data.events) {
      tbody.append(el("tr", {},
        el("td", {}, event.created_at),
        el("td", {}, event.action),
        el("td", {}, event.actor),
        el("td", {}, `${event.elapsed_ms} ms`),
        el("td", { class: "detail" }, JSON.stringify(event.detail).slice(0, 220)),
      ));
    }
  } catch (error) {
    $("events-table").querySelector("tbody").innerHTML =
      `<tr><td colspan="5">加载失败：${escapeHtml(error.message)}</td></tr>`;
  }
}

// ---------------------------------------------------------------- start
loadHealth();
loadBootstrap().catch((error) => {
  alert("初始化失败：" + error.message + "\n请按 README 启动 seekdb 与 PowerContext Server。");
});
