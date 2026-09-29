import { useEffect, useRef, useState } from "react";

const API_URL =
  import.meta.env.VITE_API_URL ||
  window.location.origin;

async function api(path, options = {}) {
  const response = await fetch(`${API_URL}${path}`, {
    credentials: "include",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

const REPORT_CONTENT_TYPE = "application/vnd.amocrm.report+json";

function formatReportValue(value, format) {
  if (value === null || value === undefined || value === "") return "—";
  if (format === "currency") {
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(value)} ₽`;
  }
  if (format === "percent") return `${value}%`;
  if (format === "timestamp") return new Date(Number(value) * 1000).toLocaleDateString("ru-RU");
  if (format === "boolean") return value ? "Да" : "Нет";
  if (format === "number") return new Intl.NumberFormat("ru-RU").format(value);
  return String(value);
}

function cleanMessageText(content = "") {
  return content
    .split("\n")
    .filter((line) => !/^Получаю данные amoCRM через [\w.-]+\.$/.test(line.trim()))
    .join("\n")
    .trim();
}

function splitMessageText(content = "", extractSource = true) {
  const lines = cleanMessageText(content).split("\n");
  if (!extractSource) return { body: lines.join("\n").trim(), source: "" };
  const sourceLines = lines.filter((line) => /^Источник:\s*amoCRM\b/i.test(line.trim()));
  return {
    body: lines.filter((line) => !/^Источник:\s*amoCRM\b/i.test(line.trim())).join("\n").trim(),
    source: sourceLines.join(" ").trim(),
  };
}

function formatMessageDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: "Europe/Moscow",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function downloadAttachment(attachment) {
  const blob = new Blob([attachment.content], { type: attachment.content_type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = attachment.filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function downloadReport(templateId, format) {
  const response = await fetch(
    `${API_URL}/api/admin/report-templates/${templateId}/export/${format}`,
    { credentials: "include" },
  );
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || "Не удалось экспортировать отчёт");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `report.${format}`;
  link.click();
  URL.revokeObjectURL(url);
}

function ReportArtifact({ attachment }) {
  let report = null;
  try {
    report = JSON.parse(attachment.content);
  } catch {
    report = null;
  }
  const [chartLimit, setChartLimit] = useState(10);
  const [chartSort, setChartSort] = useState("default");
  const [activeBar, setActiveBar] = useState(null);
  const [tableFilter, setTableFilter] = useState("");
  if (!report) return null;
  const chart = report.chart;
  const provenance = report.provenance || {};
  const validation = report.validation || {};
  let chartItems = chart
    ? chart.labels.map((label, index) => ({ label, value: Number(chart.values[index]) || 0, index }))
    : [];
  if (chartSort === "desc") chartItems = [...chartItems].sort((a, b) => b.value - a.value);
  if (chartSort === "asc") chartItems = [...chartItems].sort((a, b) => a.value - b.value);
  chartItems = chartItems.slice(0, chartLimit);
  const maxValue = chartItems.length ? Math.max(...chartItems.map((item) => item.value), 1) : 1;
  const tableRows = (report.table?.rows || []).filter((row) => (
    !tableFilter.trim()
    || Object.values(row).some((value) => String(value ?? "").toLocaleLowerCase("ru").includes(tableFilter.trim().toLocaleLowerCase("ru")))
  ));
  return (
    <section className="report-card">
      <header className="report-header">
        <div><small>amoCRM REPORT</small><h3>{report.title}</h3></div>
      </header>
      {!!report.metrics?.length && (
        <div className="report-metrics">
          {report.metrics.map((metric) => (
            <div className="report-metric" key={metric.label}>
              <span>{metric.label}</span>
              <strong>{formatReportValue(metric.value, metric.format)}</strong>
            </div>
          ))}
        </div>
      )}
      {report.plan && (
        <div className="report-plan">
          <div><strong>План / факт</strong><span>{formatReportValue(report.plan.actual, report.plan.format)} из {formatReportValue(report.plan.target, report.plan.format)} · {report.plan.completion_percent}%</span></div>
          <div className="report-plan-track"><i style={{ width: `${Math.min(Number(report.plan.completion_percent) || 0, 100)}%` }} /></div>
        </div>
      )}
      {!!report.comparison?.metrics?.length && (
        <div className="report-comparison">
          <h4>Сравнение с периодом {report.comparison.period.from} — {report.comparison.period.to}</h4>
          {report.comparison.metrics.map((metric) => (
            <div key={metric.label}>
              <span>{metric.label}</span>
              <strong className={metric.delta > 0 ? "delta-up" : metric.delta < 0 ? "delta-down" : ""}>
                {metric.delta > 0 ? "+" : ""}{formatReportValue(metric.delta, metric.format)}
                {metric.delta_percent !== null && ` · ${metric.delta_percent > 0 ? "+" : ""}${metric.delta_percent}%`}
              </strong>
            </div>
          ))}
        </div>
      )}
      {chart && (
        <div className="report-chart">
          <div className="report-chart-header">
            <h4>{chart.title}</h4>
            <div>
              <select value={chartSort} onChange={(event) => setChartSort(event.target.value)}>
                <option value="default">Исходный порядок</option>
                <option value="desc">По убыванию</option>
                <option value="asc">По возрастанию</option>
              </select>
              <select value={chartLimit} onChange={(event) => setChartLimit(Number(event.target.value))}>
                <option value="5">Топ-5</option>
                <option value="10">Топ-10</option>
                <option value="100">Все</option>
              </select>
            </div>
          </div>
          {chartItems.map((item) => (
            <button
              type="button"
              className={`chart-row interactive ${activeBar === item.index ? "active" : ""}`}
              key={`${item.label}-${item.index}`}
              onClick={() => setActiveBar(activeBar === item.index ? null : item.index)}
            >
              <span className="chart-label" title={item.label}>{item.label}</span>
              <div className="chart-track">
                <i style={{ width: `${Math.max(2, item.value / maxValue * 100)}%` }} />
              </div>
              <strong>{formatReportValue(item.value, chart.format)}</strong>
            </button>
          ))}
        </div>
      )}
      {report.table && (
        <div className="report-table-wrap">
          {report.table.rows.length > 5 && (
            <div className="report-table-filter">
              <span>⌕</span>
              <input value={tableFilter} onChange={(event) => setTableFilter(event.target.value)} placeholder="Фильтр по таблице" />
              <small>{tableRows.length} из {report.table.rows.length}</small>
            </div>
          )}
          <table className="report-table">
            <thead><tr>{report.table.columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead>
            <tbody>
              {tableRows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {report.table.columns.map((column) => (
                    <td key={column.key}>
                      {row.url && column.key === "name" ? (
                        <a href={row.url} target="_blank" rel="noreferrer">
                          {formatReportValue(row[column.key], column.format)}
                        </a>
                      ) : formatReportValue(row[column.key], column.format)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <footer className="report-provenance">
        <div>
          <span className={validation.status === "verified" ? "quality-ok" : "quality-warning"}>
            {validation.status === "verified" ? "✓ Расчёты проверены" : "⚠ Требуется проверка"}
          </span>
          {provenance.fetched_at && (
            <span>Источник: {provenance.source || "amoCRM"} · {formatMessageDate(provenance.fetched_at)} МСК</span>
          )}
          {provenance.truncated && <span>Показана сокращённая выборка</span>}
        </div>
        {!!provenance.links?.length && (
          <div className="report-source-links">
            {provenance.links.slice(0, 10).map((link) => (
              <a href={link.url} target="_blank" rel="noreferrer" key={`${link.entity}-${link.id}`}>
                {link.label || `#${link.id}`} ↗
              </a>
            ))}
          </div>
        )}
      </footer>
    </section>
  );
}

function MessageAttachments({ attachments = [] }) {
  const uniqueAttachments = attachments.filter(
    (attachment, index, items) =>
      items.findIndex(
        (item) =>
          item.content_type === attachment.content_type
          && item.content === attachment.content,
      ) === index,
  );
  return uniqueAttachments.map((attachment, index) => {
    if (attachment.content_type === REPORT_CONTENT_TYPE) {
      return <ReportArtifact attachment={attachment} key={`${attachment.filename}-${index}`} />;
    }
    if (attachment.content_type === "text/csv") {
      return (
        <button
          className="file-attachment"
          key={`${attachment.filename}-${index}`}
          onClick={() => downloadAttachment(attachment)}
        >
          <span>CSV</span><div><strong>{attachment.filename}</strong><small>Скачать данные</small></div>↓
        </button>
      );
    }
    return null;
  });
}

function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      onLogin(
        await api("/api/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        }),
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="brand-mark">AI</div>
        <h1>AI Assistant</h1>
        <p>Войдите в корпоративный аккаунт</p>
        <label>
          Логин
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} required />
        </label>
        {error && <div className="alert error">{error}</div>}
        <button disabled={loading}>{loading ? "Входим…" : "Войти"}</button>
      </form>
    </div>
  );
}

const defaultQuickActions = [
  { label: "Сделай отчёт по продажам за месяц", prompt: "Сделай отчёт по продажам за текущий месяц" },
  { label: "Покажи конверсию по воронке", prompt: "Покажи конверсию по воронке за текущий месяц" },
  { label: "Кто из менеджеров продал больше?", prompt: "Покажи рейтинг менеджеров по сумме продаж за текущий месяц" },
  { label: "Какие задачи просрочены?", prompt: "Покажи просроченные невыполненные задачи" },
];

function Chat({
  selectedModel,
  availableModels,
  onModelChange,
  conversationId,
  onConversationUpdated,
  onNewChat,
}) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [modelSearch, setModelSearch] = useState("");
  const [quickActions, setQuickActions] = useState(defaultQuickActions);
  const bottomRef = useRef(null);
  const modelPickerRef = useRef(null);

  useEffect(() => {
    api("/api/settings/quick-actions")
      .then((data) => setQuickActions(data.actions))
      .catch(() => setQuickActions(defaultQuickActions));
  }, []);

  useEffect(() => {
    if (!conversationId) {
      setMessages([]);
      return;
    }
    setLoadingHistory(true);
    api(`/api/conversations/${conversationId}/messages`)
      .then((items) => setMessages(items.map(({ role, content, attachments, created_at }) => ({
        role, content, attachments, created_at,
      }))))
      .catch((err) => setMessages([{ role: "assistant", content: `Ошибка загрузки: ${err.message}` }]))
      .finally(() => setLoadingHistory(false));
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!modelMenuOpen) return undefined;
    function closeMenu(event) {
      if (!modelPickerRef.current?.contains(event.target)) setModelMenuOpen(false);
    }
    function closeOnEscape(event) {
      if (event.key === "Escape") setModelMenuOpen(false);
    }
    document.addEventListener("mousedown", closeMenu);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeMenu);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [modelMenuOpen]);

  async function sendMessage(event) {
    event.preventDefault();
    const text = input.trim();
    await submitMessage(text, true);
  }

  async function submitMessage(text, clearInput = false) {
    if (!text || loading) return;
    if (clearInput) setInput("");
    setMessages((prev) => [...prev, {
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    }]);
    setLoading(true);
    try {
      const data = await api("/api/chat", {
        method: "POST",
        body: JSON.stringify({ conversation_id: conversationId, message: text }),
      });
      setMessages((prev) => [...prev, {
        role: "assistant",
        content: data.reply,
        attachments: data.attachments || [],
        created_at: data.assistant_message_created_at,
      }]);
      onConversationUpdated(data.conversation_id);
    } catch (err) {
      setMessages((prev) => [...prev, {
        role: "assistant",
        content: `Ошибка: ${err.message}`,
        created_at: new Date().toISOString(),
      }]);
    } finally {
      setLoading(false);
    }
  }

  function repeatRequest(assistantIndex) {
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (messages[index].role === "user") {
        submitMessage(messages[index].content);
        return;
      }
    }
  }

  function hasPreviousUserMessage(assistantIndex) {
    return messages.slice(0, assistantIndex).some((message) => message.role === "user");
  }

  async function toggleFavoriteRequest(text) {
    const existing = quickActions.findIndex((item) => item.prompt === text);
    let next;
    if (existing >= 0) {
      next = quickActions.filter((_, index) => index !== existing);
    } else {
      if (quickActions.length >= 8) return;
      next = [
        ...quickActions,
        {
          label: text.length > 77 ? `${text.slice(0, 77)}…` : text,
          prompt: text,
        },
      ];
    }
    const data = await api("/api/settings/quick-actions", {
      method: "PUT",
      body: JSON.stringify({ actions: next }),
    });
    setQuickActions(data.actions);
  }

  async function changeModel(modelCode) {
    setModelSaving(true);
    try {
      await onModelChange(modelCode);
      setModelMenuOpen(false);
      setModelSearch("");
    } finally {
      setModelSaving(false);
    }
  }

  const currentModel = availableModels.find((model) => model.code === selectedModel);
  const filteredModels = availableModels.filter((model) => {
    const query = modelSearch.trim().toLocaleLowerCase("ru");
    return !query || `${model.name} ${model.vendor} ${model.code}`.toLocaleLowerCase("ru").includes(query);
  });
  const modelGroups = filteredModels.reduce((groups, model) => {
    const vendor = model.vendor || "Другие";
    groups[vendor] = [...(groups[vendor] || []), model];
    return groups;
  }, {});

  return (
    <section className="chat-page">
      <header className="chat-topbar">
        <div className="model-picker" ref={modelPickerRef}>
          <button
            className={`model-indicator ${modelMenuOpen ? "open" : ""}`}
            type="button"
            onClick={() => setModelMenuOpen((open) => !open)}
            disabled={modelSaving}
          >
            <strong>AI Assistant</strong>
            <span>{currentModel?.name || selectedModel}</span>
            <i>⌄</i>
          </button>
          {modelMenuOpen && (
            <div className="model-menu">
              <div className="model-menu-head">
                <strong>Выберите модель</strong>
                <small>{availableModels.length} доступно</small>
              </div>
              <div className="model-search">
                <span>⌕</span>
                <input
                  autoFocus
                  value={modelSearch}
                  onChange={(event) => setModelSearch(event.target.value)}
                  placeholder="Поиск модели"
                />
              </div>
              <div className="model-options">
                {Object.entries(modelGroups).map(([vendor, models]) => (
                  <section className="model-group" key={vendor}>
                    <div className="model-group-name">{vendor}</div>
                    {models.map((model) => (
                      <button
                        type="button"
                        className={selectedModel === model.code ? "selected" : ""}
                        key={model.code}
                        onClick={() => changeModel(model.code)}
                      >
                        <span className="model-provider-dot">{model.name.charAt(0)}</span>
                        <span className="model-option-copy">
                          <strong>{model.name}</strong>
                          <small>{model.description || model.code}</small>
                        </span>
                        {model.is_light && <em>Быстрая</em>}
                        {selectedModel === model.code && <b>✓</b>}
                      </button>
                    ))}
                  </section>
                ))}
                {!filteredModels.length && <div className="model-empty">Модели не найдены</div>}
              </div>
            </div>
          )}
        </div>
        <button className="icon-button" onClick={onNewChat} title="Новый чат">＋</button>
      </header>

      <main className={`conversation ${messages.length ? "" : "empty"}`}>
        {!messages.length && !loadingHistory && (
          <div className="welcome">
            <div className="assistant-logo">AI</div>
            <h1>Чем могу помочь?</h1>
            <p>Задайте вопрос по сделкам, задачам или показателям вашей amoCRM</p>
            <div className="suggestions">
              {quickActions.map((action, index) => (
                <button key={`${action.label}-${index}`} onClick={() => setInput(action.prompt)}>
                  {action.label}<span>↗</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message, index) => (
          <article key={index} className={`message-row ${message.role}`}>
            {message.role === "assistant" && <div className="message-avatar">AI</div>}
            <div className="message-content">
              <div>{splitMessageText(message.content, message.role === "assistant").body}</div>
              <MessageAttachments attachments={message.attachments} />
              {splitMessageText(message.content, message.role === "assistant").source && (
                <small className="message-source">
                  {splitMessageText(message.content, true).source}
                </small>
              )}
              {message.created_at && (
                <time className="message-time" dateTime={message.created_at}>
                  {formatMessageDate(message.created_at)} МСК
                </time>
              )}
              {message.role === "assistant" && hasPreviousUserMessage(index) && (
                <div className="message-actions">
                  <button
                    type="button"
                    onClick={() => repeatRequest(index)}
                    disabled={loading || loadingHistory}
                    title="Отправить этот запрос повторно"
                  >
                    ↻ <span>Повторить запрос</span>
                  </button>
                </div>
              )}
              {message.role === "user" && (
                <div className="message-actions user-favorite-action">
                  <button
                    type="button"
                    onClick={() => toggleFavoriteRequest(message.content)}
                    disabled={loading || (quickActions.length >= 8 && !quickActions.some((item) => item.prompt === message.content))}
                    title={quickActions.some((item) => item.prompt === message.content) ? "Убрать из избранного" : "Добавить в быстрые кнопки"}
                  >
                    {quickActions.some((item) => item.prompt === message.content) ? "★" : "☆"} <span>В избранное</span>
                  </button>
                </div>
              )}
            </div>
          </article>
        ))}
        {loading && (
          <article className="message-row assistant">
            <div className="message-avatar">AI</div>
            <div className="thinking"><i></i><i></i><i></i></div>
          </article>
        )}
        {loadingHistory && (
          <article className="message-row assistant">
            <div className="message-avatar">AI</div>
            <div className="thinking"><i></i><i></i><i></i></div>
          </article>
        )}
        <div ref={bottomRef} />
      </main>

      <div className="composer-wrap">
        <form className="composer" onSubmit={sendMessage}>
          <textarea
            rows="1"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Сообщить AI Assistant"
            disabled={loading || loadingHistory}
          />
          <button className="send-button" disabled={loading || loadingHistory || !input.trim()} title="Отправить">↑</button>
        </form>
        <small>AI может допускать ошибки. Проверяйте важные данные в amoCRM.</small>
      </div>
    </section>
  );
}

const emptyForm = {
  name: "", email: "", password: "", role: "user", telegram_id: "",
  amocrm_user_id: "",
  request_limit_per_day: 50, is_active: true,
};

function Users() {
  const [users, setUsers] = useState([]);
  const [managers, setManagers] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState(null);
  const [error, setError] = useState("");

  async function load() {
    try {
      const [accounts, crmManagers] = await Promise.all([
        api("/api/admin/users"),
        api("/api/admin/amocrm/managers"),
      ]);
      setUsers(accounts);
      setManagers(crmManagers);
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  function edit(user) {
    setEditingId(user.id);
    setForm({
      name: user.name,
      email: user.email,
      password: "",
      role: user.role,
      telegram_id: user.telegram_id ?? "",
      amocrm_user_id: user.amocrm_user_id ?? "",
      request_limit_per_day: user.request_limit_per_day,
      is_active: user.is_active,
    });
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    const payload = {
      ...form,
      telegram_id: form.telegram_id === "" ? null : Number(form.telegram_id),
      amocrm_user_id: form.amocrm_user_id === "" ? null : Number(form.amocrm_user_id),
      request_limit_per_day: Number(form.request_limit_per_day),
    };
    if (editingId && !payload.password) delete payload.password;
    try {
      await api(editingId ? `/api/admin/users/${editingId}` : "/api/admin/users", {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      setForm(emptyForm);
      setEditingId(null);
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="admin-grid">
      <div className="panel">
        <div className="section-heading"><div><h2>Сотрудники</h2><p>{users.length} аккаунтов</p></div></div>
        <div className="user-list">
          {users.map((user) => (
            <button key={user.id} className="user-row" onClick={() => edit(user)}>
              <span className="avatar">{user.name.slice(0, 1).toUpperCase()}</span>
              <span className="user-main"><strong>{user.name}</strong><small>{user.email}</small></span>
              <span className="manager-link">{managers.find((manager) => manager.id === user.amocrm_user_id)?.name || "amoCRM не привязан"}</span>
              <span className={`badge ${user.is_active ? "active" : "blocked"}`}>{user.is_active ? "Активен" : "Отключён"}</span>
              <span className="role">{user.role === "admin" ? "Администратор" : "Пользователь"}</span>
            </button>
          ))}
        </div>
      </div>

      <form className="panel account-form" onSubmit={submit}>
        <h2>{editingId ? "Редактирование" : "Новый аккаунт"}</h2>
        <label>Имя<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
        <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required /></label>
        <label>{editingId ? "Новый пароль (необязательно)" : "Пароль"}
          <input type="password" value={form.password} minLength={10} required={!editingId} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </label>
        <div className="form-row">
          <label>Роль<select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}><option value="user">Пользователь</option><option value="admin">Администратор</option></select></label>
          <label>Лимит в день<input type="number" min="1" max="10000" value={form.request_limit_per_day} onChange={(e) => setForm({ ...form, request_limit_per_day: e.target.value })} /></label>
        </div>
        <label>Telegram ID<input type="number" value={form.telegram_id} onChange={(e) => setForm({ ...form, telegram_id: e.target.value })} placeholder="Например, 123456789" /></label>
        <label>Менеджер amoCRM
          <select value={form.amocrm_user_id} onChange={(e) => setForm({ ...form, amocrm_user_id: e.target.value })}>
            <option value="">Не привязан</option>
            {managers.map((manager) => (
              <option key={manager.id} value={manager.id}>{manager.name}{manager.email ? ` · ${manager.email}` : ""}</option>
            ))}
          </select>
        </label>
        {editingId && <label className="checkbox"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Аккаунт активен</label>}
        {error && <div className="alert error">{error}</div>}
        <div className="form-actions">
          {editingId && <button type="button" className="secondary" onClick={() => { setEditingId(null); setForm(emptyForm); }}>Отмена</button>}
          <button>{editingId ? "Сохранить" : "Создать аккаунт"}</button>
        </div>
      </form>
    </section>
  );
}

function Audit() {
  const [logs, setLogs] = useState([]);
  useEffect(() => { api("/api/admin/audit").then(setLogs).catch(() => setLogs([])); }, []);
  return (
    <section className="panel">
      <div className="section-heading"><div><h2>Журнал действий</h2><p>Последние административные события</p></div></div>
      <div className="audit-list">
        {logs.map((log) => <div className="audit-row" key={log.id}><strong>{log.action}</strong><span>Пользователь #{log.target_user_id || "—"}</span><time>{new Date(log.created_at).toLocaleString("ru-RU")}</time></div>)}
      </div>
    </section>
  );
}

function ModelSettings({ onModelChange }) {
  const [settings, setSettings] = useState(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api("/api/settings/model")
      .then((data) => {
        setSettings(data);
        setSelectedModel(data.selected_model);
        onModelChange(data.selected_model);
      })
      .catch((err) => setStatus(err.message));
  }, [onModelChange]);

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      const data = await api("/api/settings/model", {
        method: "PATCH",
        body: JSON.stringify({ selected_model: selectedModel }),
      });
      setSettings(data);
      onModelChange(data.selected_model);
      setStatus("Настройки сохранены");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setSaving(false);
    }
  }

  if (!settings) {
    return <div className="settings-loading">Загрузка настроек…</div>;
  }

  return (
    <section className="model-settings">
      <div className="subpage-title">
        <h2>Модель</h2>
        <p>Выберите модель искусственного интеллекта для всей компании</p>
      </div>
      <div className="settings-section">
        <div className="settings-copy">
          <h2>Модель</h2>
          <p>Эта модель будет использоваться в веб-чате и Telegram-боте для всех сотрудников.</p>
        </div>
        <div className="model-list">
          {settings.available_models.map((model) => (
            <label
              key={model.code}
              className={`model-card ${selectedModel === model.code ? "selected" : ""}`}
            >
              <input
                type="radio"
                name="model"
                value={model.code}
                checked={selectedModel === model.code}
                onChange={() => setSelectedModel(model.code)}
              />
              <span className="model-radio"></span>
              <span className="model-details">
                <strong>{model.name}</strong>
                <small>{model.vendor}</small>
                <p>{model.description}</p>
              </span>
            </label>
          ))}
        </div>
      </div>

      {!settings.llm_configured && (
        <div className="settings-notice">
          <strong>Модель пока не подключена</strong>
          <span>Модель можно выбрать заранее. Запросы начнут работать после добавления API-ключа.</span>
        </div>
      )}

      <div className="settings-footer">
        {status && <span className={status === "Настройки сохранены" ? "success-text" : "error-text"}>{status}</span>}
        <button onClick={save} disabled={saving || selectedModel === settings.selected_model}>
          {saving ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </section>
  );
}

function TelegramProxySettings() {
  const [form, setForm] = useState({
    name: "",
    scheme: "socks5",
    host: "",
    port: 1080,
    username: "",
    password: "",
    clear_password: false,
  });
  const [passwordConfigured, setPasswordConfigured] = useState(false);
  const [profiles, setProfiles] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [proxyEnabled, setProxyEnabled] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState("");

  async function loadProfiles() {
    return api("/api/admin/telegram-proxies")
      .then((data) => {
        setProfiles(data.profiles);
        setActiveId(data.active_id);
        setProxyEnabled(data.enabled);
      })
      .catch((err) => setStatus(err.message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadProfiles();
  }, []);

  function payload() {
    return { ...form, port: Number(form.port) };
  }

  async function testProxy() {
    setAction("test");
    setStatus("");
    try {
      const data = await api("/api/admin/telegram-proxy/test", {
        method: "POST",
        body: JSON.stringify(payload()),
      });
      setStatus(`${data.message}${data.bot_username ? ` · @${data.bot_username}` : ""}`);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  async function saveProxy(event) {
    event.preventDefault();
    setAction("save");
    setStatus("");
    try {
      const data = await api(
        editingId ? `/api/admin/telegram-proxies/${editingId}` : "/api/admin/telegram-proxies",
        {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(payload()),
        },
      );
      setPasswordConfigured(data.password_configured);
      setEditingId(data.id);
      setForm((current) => ({ ...current, password: "", clear_password: false }));
      await loadProfiles();
      setStatus("Профиль прокси сохранён");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  function editProfile(profile) {
    setEditingId(profile.id);
    setPasswordConfigured(profile.password_configured);
    setForm({
      name: profile.name,
      scheme: profile.scheme,
      host: profile.host,
      port: profile.port,
      username: profile.username,
      password: "",
      clear_password: false,
    });
    setStatus("");
  }

  function newProfile() {
    setEditingId(null);
    setPasswordConfigured(false);
    setForm({
      name: "",
      scheme: "socks5",
      host: "",
      port: 1080,
      username: "",
      password: "",
      clear_password: false,
    });
    setStatus("");
  }

  async function activateProfile(profileId) {
    setAction(`activate-${profileId}`);
    setStatus("");
    try {
      await api(`/api/admin/telegram-proxies/${profileId}/activate`, { method: "POST" });
      await loadProfiles();
      setStatus("Прокси подключён, Telegram-бот переподключается");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  async function disableProxy() {
    setAction("disable");
    setStatus("");
    try {
      await api("/api/admin/telegram-proxies/disable", { method: "POST" });
      await loadProfiles();
      setStatus("Прокси отключён, бот использует прямое соединение");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  async function testSavedProfile(profileId) {
    setAction(`test-${profileId}`);
    setStatus("");
    try {
      const data = await api(`/api/admin/telegram-proxies/${profileId}/test`, { method: "POST" });
      setStatus(`${data.message}${data.bot_username ? ` · @${data.bot_username}` : ""}`);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  async function deleteProfile(profileId) {
    if (!window.confirm("Удалить этот профиль прокси?")) return;
    setAction(`delete-${profileId}`);
    try {
      await api(`/api/admin/telegram-proxies/${profileId}`, { method: "DELETE" });
      if (editingId === profileId) newProfile();
      await loadProfiles();
      setStatus("Профиль удалён");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  if (loading) return <div className="settings-loading">Загрузка настроек…</div>;

  return (
    <section className="model-settings">
      <div className="subpage-title proxy-page-title">
        <div><h2>Telegram-прокси</h2><p>Сохранённые HTTP и SOCKS5 подключения</p></div>
        <button onClick={newProfile}>＋ Добавить прокси</button>
      </div>
      <div className={`proxy-connection-state ${proxyEnabled ? "connected" : ""}`}>
        <span>{proxyEnabled ? "●" : "○"}</span>
        <div>
          <strong>{proxyEnabled ? "Прокси подключён" : "Прямое соединение"}</strong>
          <small>
            {proxyEnabled
              ? profiles.find((profile) => profile.id === activeId)?.name || "Активный профиль"
              : "Telegram-бот подключается без прокси"}
          </small>
        </div>
        {proxyEnabled && <button className="secondary" onClick={disableProxy} disabled={!!action}>Отключить</button>}
      </div>
      <div className="proxy-profile-list">
        {profiles.map((profile) => {
          const active = proxyEnabled && profile.id === activeId;
          return (
            <article className={`proxy-profile-card ${active ? "active" : ""}`} key={profile.id}>
              <div className="proxy-profile-icon">{profile.scheme === "socks5" ? "S5" : "H"}</div>
              <div className="proxy-profile-copy">
                <div><strong>{profile.name}</strong>{active && <span>Активен</span>}</div>
                <small>{profile.scheme.toUpperCase()} · {profile.host}:{profile.port}</small>
                <small>{profile.username ? `Авторизация: ${profile.username}` : "Без авторизации"}</small>
              </div>
              <div className="proxy-profile-actions">
                {!active && <button onClick={() => activateProfile(profile.id)} disabled={!!action}>{action === `activate-${profile.id}` ? "…" : "Подключить"}</button>}
                <button className="secondary" onClick={() => testSavedProfile(profile.id)} disabled={!!action}>{action === `test-${profile.id}` ? "…" : "Проверить"}</button>
                <button className="secondary" onClick={() => editProfile(profile)} disabled={!!action}>Изменить</button>
                <button className="danger-text" onClick={() => deleteProfile(profile.id)} disabled={!!action}>×</button>
              </div>
            </article>
          );
        })}
        {!profiles.length && (
          <div className="proxy-empty">
            <strong>Прокси пока не добавлены</strong>
            <span>Создайте профиль, проверьте его и подключите к Telegram-боту.</span>
          </div>
        )}
      </div>
      <form className="settings-section proxy-settings" onSubmit={saveProxy}>
        <div className="settings-copy">
          <h2>{editingId ? "Редактирование" : "Новый профиль"}</h2>
          <p>Данные сохраняются в зашифрованном виде. После сохранения профиль можно проверить и подключить.</p>
        </div>
        <div className="proxy-grid">
          <label className="proxy-name">Название
            <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Рабочий SOCKS5" required />
          </label>
          <label>Тип
            <select value={form.scheme} onChange={(event) => setForm({ ...form, scheme: event.target.value })}>
              <option value="socks5">SOCKS5</option>
              <option value="http">HTTP</option>
            </select>
          </label>
          <label className="proxy-host">Адрес
            <input
              value={form.host}
              onChange={(event) => setForm({ ...form, host: event.target.value })}
              placeholder="proxy.example.com"
              required
            />
          </label>
          <label>Порт
            <input
              type="number"
              min="1"
              max="65535"
              value={form.port}
              onChange={(event) => setForm({ ...form, port: event.target.value })}
              required
            />
          </label>
          <label>Логин
            <input value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} autoComplete="off" />
          </label>
          <label className="proxy-password">Пароль
            <input
              type="password"
              value={form.password}
              onChange={(event) => setForm({ ...form, password: event.target.value, clear_password: false })}
              placeholder={passwordConfigured ? "Пароль сохранён — оставьте пустым" : "Необязательно"}
              autoComplete="new-password"
            />
          </label>
        </div>
        {passwordConfigured && (
          <label className="checkbox proxy-clear">
            <input
              type="checkbox"
              checked={form.clear_password}
              onChange={(event) => setForm({ ...form, clear_password: event.target.checked, password: "" })}
            />
            Удалить сохранённый пароль
          </label>
        )}
        <div className="proxy-security">
          <span>⌁</span>
          <div><strong>Пароль хранится зашифрованным</strong><small>Он не возвращается браузеру и не записывается в журнал действий.</small></div>
        </div>
        <div className="settings-footer proxy-actions">
          {status && <span className={status.includes("успешно") || status.includes("сохранён") ? "success-text" : "error-text"}>{status}</span>}
          <button type="button" className="secondary" onClick={testProxy} disabled={!!action || !form.host}>
            {action === "test" ? "Проверяем…" : "Проверить данные"}
          </button>
          <button disabled={!!action}>{action === "save" ? "Сохраняем…" : editingId ? "Сохранить изменения" : "Создать профиль"}</button>
        </div>
      </form>
    </section>
  );
}

const emptyReportForm = {
  name: "",
  report_type: "sales",
  period_mode: "current_month",
  fixed_date_from: "",
  fixed_date_to: "",
  comparison_mode: "none",
  plan_value: "",
  pipeline_id: "",
  manager_id: "",
  group_by: "status",
  top_n: 10,
  schedule_frequency: "manual",
  schedule_time: "09:00",
  schedule_weekday: 0,
  schedule_month_day: 1,
  telegram_chat_id: "",
  export_formats: ["pdf"],
  is_enabled: true,
};

function ReportScheduleSettings() {
  const [templates, setTemplates] = useState([]);
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState(emptyReportForm);
  const [editingId, setEditingId] = useState(null);
  const [preview, setPreview] = useState(null);
  const [status, setStatus] = useState("");
  const [action, setAction] = useState("");

  async function load() {
    try {
      const [saved, accounts] = await Promise.all([
        api("/api/admin/report-templates"),
        api("/api/admin/users"),
      ]);
      setTemplates(saved);
      setUsers(accounts.filter((user) => user.telegram_id));
    } catch (err) {
      setStatus(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  function payload() {
    return {
      ...form,
      fixed_date_from: form.fixed_date_from || null,
      fixed_date_to: form.fixed_date_to || null,
      plan_value: form.plan_value === "" ? null : Number(form.plan_value),
      pipeline_id: form.pipeline_id === "" ? null : Number(form.pipeline_id),
      manager_id: form.manager_id === "" ? null : Number(form.manager_id),
      top_n: Number(form.top_n),
      schedule_weekday: Number(form.schedule_weekday),
      schedule_month_day: Number(form.schedule_month_day),
      telegram_chat_id: form.telegram_chat_id === "" ? null : Number(form.telegram_chat_id),
    };
  }

  function edit(template) {
    setEditingId(template.id);
    setForm({
      name: template.name,
      report_type: template.report_type,
      period_mode: template.period_mode,
      fixed_date_from: template.fixed_date_from || "",
      fixed_date_to: template.fixed_date_to || "",
      comparison_mode: template.comparison_mode,
      plan_value: template.plan_value ?? "",
      pipeline_id: template.arguments.pipeline_id ?? "",
      manager_id: template.arguments.manager_id ?? "",
      group_by: template.arguments.group_by || "status",
      top_n: template.arguments.top_n || 10,
      schedule_frequency: template.schedule_frequency,
      schedule_time: template.schedule_time,
      schedule_weekday: template.schedule_weekday,
      schedule_month_day: template.schedule_month_day,
      telegram_chat_id: template.telegram_chat_id ?? "",
      export_formats: template.export_formats || [],
      is_enabled: template.is_enabled,
    });
    setPreview(null);
    setStatus("");
  }

  function reset() {
    setEditingId(null);
    setForm(emptyReportForm);
    setPreview(null);
    setStatus("");
  }

  async function save(event) {
    event.preventDefault();
    setAction("save");
    setStatus("");
    try {
      await api(
        editingId
          ? `/api/admin/report-templates/${editingId}`
          : "/api/admin/report-templates",
        {
          method: editingId ? "PUT" : "POST",
          body: JSON.stringify(payload()),
        },
      );
      await load();
      reset();
      setStatus("Шаблон отчёта сохранён");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  async function run(templateId, deliver = false) {
    setAction(`${deliver ? "send" : "run"}-${templateId}`);
    setStatus("");
    try {
      const data = await api(
        `/api/admin/report-templates/${templateId}/run?deliver=${deliver}`,
        { method: "POST" },
      );
      setPreview(data.presentation);
      setStatus(data.telegram_sent ? "Отчёт отправлен в Telegram" : "Отчёт сформирован");
      await load();
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  async function remove(templateId) {
    if (!window.confirm("Удалить шаблон отчёта?")) return;
    try {
      await api(`/api/admin/report-templates/${templateId}`, { method: "DELETE" });
      if (editingId === templateId) reset();
      await load();
      setStatus("Шаблон удалён");
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function exportFile(templateId, format) {
    setAction(`export-${templateId}-${format}`);
    try {
      await downloadReport(templateId, format);
      setStatus(`Отчёт ${format.toUpperCase()} подготовлен`);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setAction("");
    }
  }

  function toggleFormat(format) {
    setForm({
      ...form,
      export_formats: form.export_formats.includes(format)
        ? form.export_formats.filter((item) => item !== format)
        : [...form.export_formats, format],
    });
  }

  return (
    <section className="model-settings">
      <div className="subpage-title proxy-page-title">
        <div><h2>Отчётность</h2><p>Шаблоны, расписание и доставка в Telegram</p></div>
        <button onClick={reset}>＋ Новый шаблон</button>
      </div>

      <div className="report-template-list">
        {templates.map((template) => (
          <article className="report-template-card" key={template.id}>
            <div className="report-template-icon">
              {template.report_type === "sales" ? "₽" : template.report_type === "funnel" ? "▽" : "◎"}
            </div>
            <div className="report-template-copy">
              <div><strong>{template.name}</strong><span className={`badge ${template.is_enabled ? "active" : "blocked"}`}>{template.is_enabled ? "Активен" : "Пауза"}</span></div>
              <small>{template.schedule_frequency === "manual" ? "Ручной запуск" : `${template.schedule_frequency} · ${template.schedule_time} МСК`}</small>
              <small>{template.last_status ? `Последний запуск: ${template.last_status}` : "Ещё не запускался"}</small>
            </div>
            <div className="report-template-actions">
              <button onClick={() => run(template.id)} disabled={!!action}>Сформировать</button>
              {template.telegram_chat_id && <button className="secondary" onClick={() => run(template.id, true)} disabled={!!action}>В Telegram</button>}
              <button className="secondary" onClick={() => exportFile(template.id, "xlsx")} disabled={!!action}>XLSX</button>
              <button className="secondary" onClick={() => exportFile(template.id, "pdf")} disabled={!!action}>PDF</button>
              <button className="secondary" onClick={() => edit(template)} disabled={!!action}>Изменить</button>
              <button className="danger-text" onClick={() => remove(template.id)} disabled={!!action}>×</button>
            </div>
          </article>
        ))}
        {!templates.length && <div className="proxy-empty"><strong>Шаблонов пока нет</strong><span>Создайте первый регулярный отчёт.</span></div>}
      </div>

      <form className="settings-section report-template-form" onSubmit={save}>
        <div className="settings-copy">
          <h2>{editingId ? "Редактирование шаблона" : "Новый шаблон"}</h2>
          <p>Расчёты выполняются напрямую по данным amoCRM без участия языковой модели.</p>
        </div>
        <div className="report-template-grid">
          <label className="report-wide">Название<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
          <label>Тип отчёта<select value={form.report_type} onChange={(e) => setForm({ ...form, report_type: e.target.value })}><option value="sales">Продажи</option><option value="funnel">Воронка</option><option value="managers">Менеджеры</option></select></label>
          <label>Период<select value={form.period_mode} onChange={(e) => setForm({ ...form, period_mode: e.target.value })}><option value="current_month">Текущий месяц</option><option value="previous_month">Прошлый месяц</option><option value="last_7_days">Последние 7 дней</option><option value="last_30_days">Последние 30 дней</option><option value="fixed">Фиксированные даты</option></select></label>
          {form.period_mode === "fixed" && <>
            <label>Дата начала<input type="date" value={form.fixed_date_from} onChange={(e) => setForm({ ...form, fixed_date_from: e.target.value })} required /></label>
            <label>Дата окончания<input type="date" value={form.fixed_date_to} onChange={(e) => setForm({ ...form, fixed_date_to: e.target.value })} required /></label>
          </>}
          <label>Сравнение<select value={form.comparison_mode} onChange={(e) => setForm({ ...form, comparison_mode: e.target.value })}><option value="none">Без сравнения</option><option value="previous_period">С предыдущим периодом</option></select></label>
          <label>План<input type="number" min="0" value={form.plan_value} onChange={(e) => setForm({ ...form, plan_value: e.target.value })} placeholder="Необязательно" /></label>
          {form.report_type === "sales" && <label>Группировка<select value={form.group_by} onChange={(e) => setForm({ ...form, group_by: e.target.value })}><option value="status">По этапам</option><option value="manager">По менеджерам</option><option value="day">По дням</option><option value="week">По неделям</option></select></label>}
          {form.report_type !== "managers" && <label>ID воронки<input type="number" min="1" value={form.pipeline_id} onChange={(e) => setForm({ ...form, pipeline_id: e.target.value })} /></label>}
          {form.report_type === "sales" && <label>ID менеджера<input type="number" min="1" value={form.manager_id} onChange={(e) => setForm({ ...form, manager_id: e.target.value })} /></label>}
          {form.report_type === "managers" && <label>Количество менеджеров<input type="number" min="1" max="50" value={form.top_n} onChange={(e) => setForm({ ...form, top_n: e.target.value })} /></label>}
          <label>Расписание<select value={form.schedule_frequency} onChange={(e) => setForm({ ...form, schedule_frequency: e.target.value })}><option value="manual">Только вручную</option><option value="daily">Каждый день</option><option value="weekly">Каждую неделю</option><option value="monthly">Каждый месяц</option></select></label>
          {form.schedule_frequency !== "manual" && <label>Время по МСК<input type="time" value={form.schedule_time} onChange={(e) => setForm({ ...form, schedule_time: e.target.value })} /></label>}
          {form.schedule_frequency === "weekly" && <label>День недели<select value={form.schedule_weekday} onChange={(e) => setForm({ ...form, schedule_weekday: e.target.value })}><option value="0">Понедельник</option><option value="1">Вторник</option><option value="2">Среда</option><option value="3">Четверг</option><option value="4">Пятница</option><option value="5">Суббота</option><option value="6">Воскресенье</option></select></label>}
          {form.schedule_frequency === "monthly" && <label>День месяца<input type="number" min="1" max="28" value={form.schedule_month_day} onChange={(e) => setForm({ ...form, schedule_month_day: e.target.value })} /></label>}
          <label className="report-wide">Получатель Telegram<select value={form.telegram_chat_id} onChange={(e) => setForm({ ...form, telegram_chat_id: e.target.value })}><option value="">Не отправлять</option>{users.map((user) => <option key={user.id} value={user.telegram_id}>{user.name} · {user.telegram_id}</option>)}</select></label>
        </div>
        <div className="report-format-row">
          <span>Файлы для Telegram:</span>
          {["xlsx", "pdf", "png"].map((format) => <label className="checkbox" key={format}><input type="checkbox" checked={form.export_formats.includes(format)} onChange={() => toggleFormat(format)} />{format.toUpperCase()}</label>)}
          <label className="checkbox"><input type="checkbox" checked={form.is_enabled} onChange={(e) => setForm({ ...form, is_enabled: e.target.checked })} />Шаблон активен</label>
        </div>
        <div className="settings-footer">
          {status && <span className={status.includes("не ") || status.includes("ошиб") ? "error-text" : "success-text"}>{status}</span>}
          {editingId && <button type="button" className="secondary" onClick={reset}>Отмена</button>}
          <button disabled={!!action}>{action === "save" ? "Сохраняем…" : "Сохранить шаблон"}</button>
        </div>
      </form>

      {preview && <ReportArtifact attachment={{ content: JSON.stringify(preview) }} />}
    </section>
  );
}

function QuickActionsSettings() {
  const [actions, setActions] = useState([]);
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api("/api/settings/quick-actions")
      .then((data) => setActions(data.actions))
      .catch((err) => setStatus(err.message));
  }, []);

  function update(index, key, value) {
    setActions(actions.map((item, itemIndex) => (
      itemIndex === index ? { ...item, [key]: value } : item
    )));
  }

  function move(index, direction) {
    const target = index + direction;
    if (target < 0 || target >= actions.length) return;
    const next = [...actions];
    [next[index], next[target]] = [next[target], next[index]];
    setActions(next);
  }

  async function save() {
    setSaving(true);
    setStatus("");
    try {
      const data = await api("/api/settings/quick-actions", {
        method: "PUT",
        body: JSON.stringify({ actions }),
      });
      setActions(data.actions);
      setStatus("Быстрые кнопки сохранены");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    setSaving(true);
    setStatus("");
    try {
      const data = await api("/api/settings/quick-actions", { method: "DELETE" });
      setActions(data.actions);
      setStatus("Восстановлен стандартный набор");
    } catch (err) {
      setStatus(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="model-settings">
      <div className="subpage-title proxy-page-title">
        <div><h2>Быстрые кнопки</h2><p>Персональные подсказки на стартовом экране чата</p></div>
        <button
          onClick={() => setActions([...actions, { label: "", prompt: "" }])}
          disabled={actions.length >= 8}
        >
          ＋ Добавить
        </button>
      </div>
      <div className="quick-actions-editor">
        {actions.map((action, index) => (
          <article className="quick-action-editor" key={index}>
            <span className="quick-action-number">{index + 1}</span>
            <div className="quick-action-fields">
              <label>Текст на кнопке
                <input
                  value={action.label}
                  maxLength={80}
                  onChange={(event) => update(index, "label", event.target.value)}
                  placeholder="Например: Продажи за неделю"
                />
              </label>
              <label>Запрос, который отправится в чат
                <textarea
                  rows="2"
                  value={action.prompt}
                  maxLength={1000}
                  onChange={(event) => update(index, "prompt", event.target.value)}
                  placeholder="Сделай отчёт по продажам за последние 7 дней"
                />
              </label>
            </div>
            <div className="quick-action-controls">
              <button className="secondary" onClick={() => move(index, -1)} disabled={index === 0}>↑</button>
              <button className="secondary" onClick={() => move(index, 1)} disabled={index === actions.length - 1}>↓</button>
              <button className="danger-text" onClick={() => setActions(actions.filter((_, itemIndex) => itemIndex !== index))}>×</button>
            </div>
          </article>
        ))}
        {!actions.length && (
          <div className="proxy-empty">
            <strong>Быстрые кнопки скрыты</strong>
            <span>Добавьте новую кнопку или восстановите стандартный набор.</span>
          </div>
        )}
      </div>
      <div className="settings-footer">
        {status && <span className={status.includes("сохранены") || status.includes("Восстановлен") ? "success-text" : "error-text"}>{status}</span>}
        <button className="secondary" onClick={reset} disabled={saving}>По умолчанию</button>
        <button onClick={save} disabled={saving || actions.some((item) => !item.label.trim() || !item.prompt.trim())}>
          {saving ? "Сохраняем…" : "Сохранить"}
        </button>
      </div>
    </section>
  );
}

function AmoCRMIntegrationSettings() {
  const [status, setStatus] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [insights, setInsights] = useState([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      const [statusData, metricsData, insightData] = await Promise.all([
        api("/api/admin/amocrm/status").catch((err) => ({ message: err.message })),
        api("/api/admin/amocrm/metrics"),
        api("/api/admin/amocrm/insights?limit=50"),
      ]);
      setStatus(statusData);
      setMetrics(metricsData);
      setInsights(insightData);
    } catch (err) {
      setMessage(err.message);
    }
  }

  useEffect(() => { load(); }, []);

  async function run(action) {
    setBusy(true);
    setMessage("");
    try {
      const data = await api(action === "register"
        ? "/api/admin/amocrm/webhook/register"
        : "/api/admin/amocrm/scan", { method: "POST" });
      setMessage(action === "register"
        ? "Webhook зарегистрирован в amoCRM"
        : `Проверено: ${data.scanned}, найдено: ${data.found}, высокий риск: ${data.high_risk}`);
      await load();
    } catch (err) {
      setMessage(err.message);
    } finally {
      setBusy(false);
    }
  }

  const highRisk = insights.filter((item) => item.risk_level === "high").length;
  return (
    <section className="model-settings">
      <div className="subpage-title proxy-page-title">
        <div><h2>Интеграция amoCRM</h2><p>Оперативные обновления и контроль забытых сделок</p></div>
        <button onClick={() => run("scan")} disabled={busy}>⌕ Проверить сделки</button>
      </div>
      <div className="amo-integration-summary">
        <article>
          <span className={`amo-state ${status?.registered ? "active" : ""}`} />
          <div><strong>Webhooks</strong><small>{status?.registered ? "Подключены и принимают изменения" : status?.message || "Не зарегистрированы"}</small></div>
          <button className="secondary" onClick={() => run("register")} disabled={busy || !status?.configured}>Подключить</button>
        </article>
        <article>
          <strong>{metrics?.requests_today || 0}</strong>
          <span>API-запросов сегодня · очередь {metrics?.queue_size || 0}</span>
        </article>
        <article>
          <strong>{metrics?.circuit_open ? "Пауза" : "Работает"}</strong>
          <span>{metrics?.circuit_open ? `${metrics.circuit_reason}, ${metrics.circuit_seconds} сек.` : "Защита API активна"}</span>
        </article>
        <article><strong>{insights.length}</strong><span>сделок требуют внимания</span></article>
        <article><strong>{highRisk}</strong><span>с высоким риском</span></article>
      </div>
      {status?.public_base_url && <div className="security-note"><strong>Адрес сервера</strong><span>{status.public_base_url}</span></div>}
      {message && <p className={message.includes("зарегистрирован") || message.includes("Проверено") ? "success-text" : "error-text"}>{message}</p>}
      <div className="amo-insight-list">
        {insights.map((item) => (
          <article className={`amo-insight-card risk-${item.risk_level}`} key={item.lead_id}>
            <div className="amo-risk-score"><strong>{item.risk_score}</strong><span>риск</span></div>
            <div>
              <a href={item.url} target="_blank" rel="noreferrer">{item.name}</a>
              <small>{item.reasons.join(" · ") || "Профилактический контроль"}</small>
              <p>{item.recommendations.join(" · ")}</p>
            </div>
            <div className="amo-communication">
              <span>☎ {item.communication?.calls?.total || 0}</span>
              <span>✉ {item.communication?.messages?.total || 0}</span>
              <span>Задач: {item.source?.open_tasks || 0}</span>
            </div>
          </article>
        ))}
        {!insights.length && <div className="proxy-empty"><strong>Рискованные сделки пока не найдены</strong><span>Запустите проверку, чтобы получить актуальные рекомендации.</span></div>}
      </div>
    </section>
  );
}

function SettingsHub({ isAdmin, onModelChange, onLogout }) {
  const [tab, setTab] = useState("model");

  return (
    <section className="settings-hub">
      <header className="settings-header">
        <div>
          <h1>Настройки</h1>
          <p>Управление рабочим пространством AI Assistant</p>
        </div>
        <button className="logout-button" onClick={onLogout}>Выйти</button>
      </header>
      <div className="settings-layout">
        <nav className="settings-nav">
          <button className={tab === "model" ? "selected" : ""} onClick={() => setTab("model")}>
            <span>◇</span><div><strong>Модель</strong><small>Настройки модели</small></div>
          </button>
          <button className={tab === "quick" ? "selected" : ""} onClick={() => setTab("quick")}>
            <span>⚡</span><div><strong>Быстрые кнопки</strong><small>Персональные запросы</small></div>
          </button>
          {isAdmin && (
            <button className={tab === "telegram" ? "selected" : ""} onClick={() => setTab("telegram")}>
              <span>↗</span><div><strong>Telegram</strong><small>Прокси и соединение</small></div>
            </button>
          )}
          {isAdmin && (
            <button className={tab === "amocrm" ? "selected" : ""} onClick={() => setTab("amocrm")}>
              <span>↻</span><div><strong>amoCRM</strong><small>Webhooks и рекомендации</small></div>
            </button>
          )}
          {isAdmin && (
            <button className={tab === "reports" ? "selected" : ""} onClick={() => setTab("reports")}>
              <span>▥</span><div><strong>Отчётность</strong><small>Шаблоны и расписание</small></div>
            </button>
          )}
          {isAdmin && (
            <button className={tab === "users" ? "selected" : ""} onClick={() => setTab("users")}>
              <span>◎</span><div><strong>Аккаунты</strong><small>Сотрудники и доступ</small></div>
            </button>
          )}
          {isAdmin && (
            <button className={tab === "audit" ? "selected" : ""} onClick={() => setTab("audit")}>
              <span>≡</span><div><strong>Журнал</strong><small>История действий</small></div>
            </button>
          )}
        </nav>
        <div className="settings-body">
          {tab === "model" && <ModelSettings onModelChange={onModelChange} />}
          {tab === "quick" && <QuickActionsSettings />}
          {tab === "telegram" && isAdmin && <TelegramProxySettings />}
          {tab === "amocrm" && isAdmin && <AmoCRMIntegrationSettings />}
          {tab === "reports" && isAdmin && <ReportScheduleSettings />}
          {tab === "users" && isAdmin && <Users />}
          {tab === "audit" && isAdmin && <Audit />}
        </div>
      </div>
    </section>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = useState("chat");
  const [selectedModel, setSelectedModel] = useState("gpt-4.1-mini");
  const [availableModels, setAvailableModels] = useState([
    { code: "gpt-4.1-mini", name: "gpt-4.1-mini" },
  ]);
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [historySearch, setHistorySearch] = useState("");
  const [editingConversationId, setEditingConversationId] = useState(null);
  const [editingConversationTitle, setEditingConversationTitle] = useState("");

  useEffect(() => {
    api("/api/auth/me").then(setUser).catch(() => setUser(null)).finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (user) {
      api("/api/settings/model")
        .then((data) => {
          setSelectedModel(data.selected_model);
          setAvailableModels(data.available_models);
        })
        .catch(() => null);
      loadConversations();
    }
  }, [user]);

  useEffect(() => {
    if (!user) return undefined;
    const timer = window.setTimeout(async () => {
      try {
        const query = historySearch.trim();
        setConversations(
          await api(`/api/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`),
        );
      } catch {
        setConversations([]);
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [historySearch, user]);

  async function loadConversations() {
    try {
      const query = historySearch.trim();
      setConversations(
        await api(`/api/conversations${query ? `?q=${encodeURIComponent(query)}` : ""}`),
      );
    } catch {
      setConversations([]);
    }
  }

  function newChat() {
    setActiveConversationId(null);
    setPage("chat");
  }

  async function conversationUpdated(conversationId) {
    setActiveConversationId(conversationId);
    await loadConversations();
  }

  async function changeChatModel(modelCode) {
    const data = await api("/api/settings/model", {
      method: "PATCH",
      body: JSON.stringify({ selected_model: modelCode }),
    });
    setSelectedModel(data.selected_model);
    setAvailableModels(data.available_models);
  }

  async function deleteConversation(event, conversationId) {
    event.stopPropagation();
    await api(`/api/conversations/${conversationId}`, { method: "DELETE" });
    if (activeConversationId === conversationId) {
      setActiveConversationId(null);
    }
    await loadConversations();
  }

  async function pinConversation(event, conversation) {
    event.stopPropagation();
    await api(`/api/conversations/${conversation.id}/pin`, {
      method: "PATCH",
      body: JSON.stringify({ is_pinned: !conversation.is_pinned }),
    });
    await loadConversations();
  }

  function startRename(event, conversation) {
    event.stopPropagation();
    setEditingConversationId(conversation.id);
    setEditingConversationTitle(conversation.title);
  }

  async function finishRename(conversationId) {
    const title = editingConversationTitle.trim();
    if (title) {
      await api(`/api/conversations/${conversationId}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
    }
    setEditingConversationId(null);
    setEditingConversationTitle("");
    await loadConversations();
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    setUser(null);
  }

  if (checking) return <div className="splash">Загрузка…</div>;
  if (!user) return <Login onLogin={setUser} />;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="logo"><span>AI</span><div><strong>AI Assistant</strong><small>amoCRM analytics</small></div></div>
        <nav>
          <button className={`new-chat ${page === "chat" && !activeConversationId ? "selected" : ""}`} onClick={newChat}><span>＋</span> Новый чат</button>
        </nav>
        <div className="chat-history">
          <div className="history-label">Ваши чаты</div>
          <div className="history-search">
            <span>⌕</span>
            <input
              value={historySearch}
              onChange={(event) => setHistorySearch(event.target.value)}
              placeholder="Поиск по истории"
            />
            {historySearch && <button onClick={() => setHistorySearch("")}>×</button>}
          </div>
          {conversations.map((conversation) => (
            <div
              className={`history-row ${activeConversationId === conversation.id && page === "chat" ? "selected" : ""}`}
              key={conversation.id}
            >
              {editingConversationId === conversation.id ? (
                <input
                  className="history-rename-input"
                  autoFocus
                  value={editingConversationTitle}
                  onChange={(event) => setEditingConversationTitle(event.target.value)}
                  onBlur={() => finishRename(conversation.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") finishRename(conversation.id);
                    if (event.key === "Escape") setEditingConversationId(null);
                  }}
                />
              ) : (
                <button
                  className="history-open"
                  onClick={() => {
                    setActiveConversationId(conversation.id);
                    setPage("chat");
                  }}
                  title={conversation.title}
                >
                  {conversation.is_pinned && <span className="history-pin-mark">◆</span>}
                  {conversation.title}
                </button>
              )}
              <div className="history-actions">
                <button onClick={(event) => pinConversation(event, conversation)} title={conversation.is_pinned ? "Открепить" : "Закрепить"}>{conversation.is_pinned ? "◆" : "◇"}</button>
                <button onClick={(event) => startRename(event, conversation)} title="Переименовать">✎</button>
                <button onClick={(event) => deleteConversation(event, conversation.id)} title="Удалить чат">×</button>
              </div>
            </div>
          ))}
          {!conversations.length && historySearch && <div className="history-empty">Ничего не найдено</div>}
        </div>
        <div className="profile">
          <span className="profile-avatar">{user.name.slice(0, 1).toUpperCase()}</span>
          <span className="profile-copy"><strong>{user.name}</strong><small>{user.email}</small></span>
          <button
            className="profile-settings"
            onClick={() => setPage("settings")}
            title="Настройки"
          >
            ⚙
          </button>
        </div>
      </aside>
      <div className="content">
        {page === "chat" && (
          <Chat
            selectedModel={selectedModel}
            availableModels={availableModels}
            onModelChange={changeChatModel}
            conversationId={activeConversationId}
            onConversationUpdated={conversationUpdated}
            onNewChat={newChat}
          />
        )}
        {page === "settings" && (
          <SettingsHub
            isAdmin={user.role === "admin"}
            onModelChange={setSelectedModel}
            onLogout={logout}
          />
        )}
      </div>
    </div>
  );
}
