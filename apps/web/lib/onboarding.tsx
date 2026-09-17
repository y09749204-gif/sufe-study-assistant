"use client";
import { useEffect, useRef, useState } from "react";
import { fetchJson } from "./api";
import AISettings, {clearAISecrets} from "./ai-settings";
import ReplayMode from "./replay-mode";

type Row = {
  course_code: string;
  name: string;
  section_code: string;
  weekday: number;
  start: string;
  end: string;
  weeks: string;
  location: string;
  teacher: string;
  canvas_id: string;
};
type Draft = {
  storage_root: string;
  term_name: string;
  starts_on: string;
  teaching_weeks: number;
  replay_mode: string;
  periods: { number: number; start: string; end: string }[];
  rows: Row[];
};
const blank: Row = {
  course_code: "",
  name: "",
  section_code: "default",
  weekday: 0,
  start: "08:00",
  end: "09:40",
  weeks: "1",
  location: "",
  teacher: "",
  canvas_id: "",
};
const steps = [
  "欢迎与检查",
  "资料目录",
  "学期与课表",
  "连接学校平台",
  "学习功能",
  "准备完成",
];
const request = (path: string, body: unknown = {}, method = "POST") =>
  fetchJson<any>(path, {
    method,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(660000),
  });
const rowDraft = (rows: any[]): Row[] =>
  rows.map((r) => ({
    ...blank,
    ...r,
    start: r.start?.slice(0, 5) || "08:00",
    end: r.end?.slice(0, 5) || "09:40",
    weeks: Array.isArray(r.weeks) ? r.weeks.join(",") : String(r.weeks || "1"),
  }));
const statusText = (s: any) =>
  ({
    never_run: "尚未连接",
    authenticated: "登录检查通过",
    login_required: "登录已过期，请重新登录",
    login_window_closed: "请检查登录状态",
    syncing: "正在处理",
    success: "已完成",
    synced: "已完成",
    failed: "失败，请重试",
    partial: "部分失败",
    no_recordings: "暂无匹配回放",
    discovered: "已发现回放",
  })[s?.status as string] ||
  s?.message ||
  "尚未检查";

export default function Onboarding({
  onDone,
}: {
  onDone: () => Promise<void>;
}) {
  const [draft, setDraft] = useState<Draft>({
    storage_root: "",
    term_name: "新学期",
    starts_on: "",
    teaching_weeks: 18,
    replay_mode: "text",
    periods: [],
    rows: [],
  });
  const [step, setStep] = useState(0),
    [loaded, setLoaded] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [saveState, setSaveState] = useState("");
  const [settings, setSettings] = useState<any>({}),
    [environment, setEnvironment] = useState<any>(null),
    [disk, setDisk] = useState<any>(null),
    [warnings, setWarnings] = useState<string[]>([]),
    [reviewed, setReviewed] = useState(false);
  const [courses, setCourses] = useState<any[]>([]),
    [events, setEvents] = useState<any[]>([]),
    [identity, setIdentity] = useState<any>({}),
    [platform, setPlatform] = useState<any>({}),
    [selected, setSelected] = useState<string[]>([]),
    [syncResults, setSyncResults] = useState<Record<string, string>>({});
  const [ai, setAi] = useState<any>({
    provider: "none",
    base_url: "http://127.0.0.1:11434",
    text_model: "",
    vision_model: "",
    api_key: "",
    cloud_consent: false,
  });
  const [syncBatches, setSyncBatches] = useState<Record<string, string>>({});
  const [queueTasks, setQueueTasks] = useState<any[]>([]);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const title = useRef<HTMLHeadingElement>(null);
  const latest = useRef({ draft, step });
  latest.current = { draft, step };
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    const [s, c, e] = await Promise.all([
      fetchJson<any>("/api/setup"),
      fetchJson<any[]>("/api/academics/courses"),
      fetchJson<any[]>("/api/calendar"),
    ]);
    setSettings(s);
    setCourses(c);
    setEvents(e);
    return s;
  }
  function persist(
    value = latest.current.draft,
    position = latest.current.step,
  ) {
    setSaveState("正在保存草稿…");
    const snapshot = JSON.parse(
      JSON.stringify({ step: position, draft: value }),
    );
    const operation = queue.current
      .catch(() => {})
      .then(() => request("/api/onboarding", snapshot, "PUT"));
    queue.current = operation;
    return operation.then(
      () => {
        setSaveState("草稿已保存到本机");
      },
      (e) => {
        setSaveState("草稿保存失败，请重试");
        throw e;
      },
    );
  }
  useEffect(() => {
    void action(async () => {
      const [state, s] = await Promise.all([
        fetchJson<any>("/api/onboarding"),
        refresh(),
      ]);
      const savedDraft = { ...state.draft };
      if (state.completed) {
        for (const key of [
          "storage_root",
          "term_name",
          "starts_on",
          "teaching_weeks",
          "periods",
          "replay_mode",
        ]) {
          if (s.settings[key] !== undefined) savedDraft[key] = s.settings[key];
        }
      }
      setDraft({
        ...draft,
        ...savedDraft,
        storage_root:
          savedDraft.storage_root ||
          s.settings.storage_root ||
          s.default_storage_root,
        rows: rowDraft(savedDraft.rows || []),
      });
      setStep(state.step);
      setAi(clearAISecrets({ ...ai, ...s.settings.ai }));
      setLoaded(true);
    });
  }, []);
  useEffect(() => {
    if (!loaded) return;
    const timer = setTimeout(() => {
      void persist().catch(() => {});
    }, 400);
    saveTimer.current = timer;
    return () => clearTimeout(timer);
  }, [draft, step, loaded]);
  useEffect(() => {
    title.current?.focus();
    if (loaded && (step === 0 || step === 3 || step === 5))
      void action(async () => {
        if (step === 0)
          setEnvironment(await fetchJson("/api/onboarding/environment"));
        else await connections();
      });
  }, [step, loaded]);
  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setSaveState("有更改，正在保存…");
    setDraft((d) => ({ ...d, [key]: value }));
    if (["rows", "periods", "starts_on", "teaching_weeks"].includes(key))
      setReviewed(false);
    if (key === "storage_root") setDisk(null);
  }
  async function go(next: number) {
    clearTimeout(saveTimer.current);
    await persist(latest.current.draft, next);
    setStep(next);
    setNotice("");
    setError("");
  }
  async function checkStorage() {
    const result = await request("/api/onboarding/storage", {
      path: draft.storage_root,
    });
    setDisk(result);
    if (!result.sufficient)
      throw Error("剩余空间不足 512 MB，请更换目录或释放空间");
    return result;
  }
  async function saveTerm() {
    const { rows, ...term } = draft;
    await request("/api/setup", term);
    await refresh();
    setNotice("学期设置已保存，可以校验并导入课表");
  }
  async function next() {
    if (step === 1) await checkStorage();
    if (step === 2) await saveTerm();
    if (step === 4) {
      await request("/api/setup/ai", ai, "PUT");
      setAi(clearAISecrets(ai));
      await saveTerm();
    }
    await go(step + 1);
  }
  const payload = () => ({
    rows: draft.rows.map((r) => ({
      ...r,
      weeks: r.weeks
        .split(/[,，\s]+/)
        .filter(Boolean)
        .map(Number),
    })),
  });
  async function preview() {
    await saveTerm();
    const result = await request("/api/setup/timetable/preview", payload());
    update("rows", rowDraft(result.rows));
    setWarnings(result.warnings);
    setReviewed(true);
    setNotice("校验完成。请核对每一行，确认后才会生成日历。");
  }
  async function importFile(file: File, image = false) {
    if (image) {
      if (!(settings.settings?.ai?.vision_reuse === false ? settings.settings?.ai?.vision_service?.vision_model : settings.settings?.ai?.vision_model))
        throw Error(
          "截图识别需要看图模型。请先前往第 5 步配置并保存 AI，再返回这里。",
        );
      await saveTerm();
      const data = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const r = await request("/api/setup/timetable/recognize", {
        image: data,
      });
      update("rows", rowDraft(r.rows));
      setWarnings([...r.warnings, r.notice]);
    } else {
      await saveTerm();
      const r = await request("/api/setup/timetable/csv", {
        text: await file.text(),
      });
      update("rows", rowDraft(r.rows));
      setWarnings(r.warnings);
    }
    setReviewed(false);
    setNotice("已载入草稿，尚未写入课程或日历");
  }
  async function connections() {
    const endpoints = [
      "/api/connections/canvas/identity",
      "/api/academics/canvas/status",
      "/api/academics/kzkt/status",
      "/api/academics/kzkt/queue",
    ];
    const result = await Promise.allSettled(
      endpoints.map((p) => fetchJson<any>(p)),
    );
    if (result[0].status === "fulfilled") setIdentity(result[0].value);
    setPlatform({
      canvas:
        result[1].status === "fulfilled"
          ? result[1].value
          : { message: "状态读取失败，可重试" },
      kzkt:
        result[2].status === "fulfilled"
          ? result[2].value
          : { message: "状态读取失败，可重试" },
    });
    if (result[3].status === "fulfilled")
      setQueueTasks(result[3].value.tasks || []);
    await refresh();
  }
  async function sync() {
    const result: Record<string, string> = {};
    setSyncResults({});
    for (const id of selected) {
      try {
        const r = await request("/api/academics/kzkt/queue/batches", {
          course_id: id,
        });
        setSyncBatches((b) => ({ ...b, [id]: r.batch_id }));
        result[id] = `已加入队列（${r.courses} 门课程），尚未处理完成`;
      } catch (e) {
        result[id] = `失败：${e instanceof Error ? e.message : String(e)}`;
      }
      setSyncResults({ ...result });
    }
  }
  function syncStatus(id: string, fallback: string) {
    const tasks = queueTasks.filter((t) =>
      t.batches?.includes(syncBatches[id]),
    );
    if (!tasks.length) return fallback;
    const failed = tasks.find((t) =>
      ["failed", "blocked", "needs_confirmation"].includes(t.status),
    );
    if (failed) return `需要处理：${failed.error || "请前往处理任务查看详情"}`;
    if (tasks.every((t) => ["success", "superseded"].includes(t.status)))
      return "处理完成";
    return `处理中：${tasks.filter((t) => t.status === "success").length} / ${tasks.length} 项完成`;
  }
  useEffect(() => {
    if (step !== 5 || !Object.keys(syncBatches).length) return;
    const timer = setInterval(() => {
      void connections().catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [step, syncBatches]);
  if (!loaded)
    return (
      <div className="onboarding">
        <p role="status">正在读取首次设置…</p>
        {error && (
          <p role="alert" className="alert">
            {error}
          </p>
        )}
        <button onClick={() => location.reload()}>重新连接</button>
      </div>
    );
  return (
    <div className="onboarding">
      <header className="onboarding-header">
        <div>
          <span className="eyebrow">上财学业助手 · 开发预览版</span>
          <h1 ref={title} tabIndex={-1}>
            {steps[step]}
          </h1>
        </div>
        <span role="status">{saveState}</span>
      </header>
      <nav aria-label="首次设置步骤" className="onboarding-steps">
        {steps.map((name, i) => (
          <button
            key={name}
            disabled={busy}
            aria-current={step === i ? "step" : undefined}
            onClick={() => void action(() => go(i))}
          >
            <b>{i + 1}</b>
            {name}
          </button>
        ))}
      </nav>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <fieldset disabled={busy} className="onboarding-body">
        {step === 0 && (
          <section>
            <h2>先准备好你的学习工作台</h2>
            <p>
              课程、日历和资料保存在这台电脑。学校登录资料使用本应用的独立目录；云端
              AI 默认关闭。设置中途退出，下次会继续已保存的进度。
            </p>
            <p>当前版本仍在验收中，请保留重要资料的原始副本。</p>
            {environment?.checks.map((c: any) => (
              <article key={c.name}>
                <strong>{c.name}</strong>
                <span>
                  {c.ok ? "✓ " : ""}
                  {c.message}
                </span>
              </article>
            ))}
            <p>应用数据目录：{environment?.data_directory || "正在检查…"}</p>
            <button
              onClick={() =>
                void action(async () =>
                  setEnvironment(
                    await fetchJson("/api/onboarding/environment"),
                  ),
                )
              }
            >
              重新检查环境
            </button>
            <p>
              没有浏览器仍可配置课表，连接学校平台时再安装{" "}
              <a
                href="https://www.microsoft.com/edge/download"
                target="_blank"
                rel="noreferrer"
              >
                Edge
              </a>{" "}
              或{" "}
              <a
                href="https://www.google.com/chrome/"
                target="_blank"
                rel="noreferrer"
              >
                Chrome
              </a>
              。
            </p>
          </section>
        )}
        {step === 1 && (
          <section>
            <h2>课程资料保存在哪里？</h2>
            <p>
              可以使用 C
              盘或其他磁盘。视频回放占用较大，建议预留更多空间。数据库与登录凭据由应用管理，无需自己设置。
            </p>
            <label>
              资料目录
              <input
                value={draft.storage_root}
                readOnly={!!settings.configured}
                onChange={(e) => update("storage_root", e.target.value)}
              />
            </label>
            {settings.configured ? (
              <p>资料目录已启用。迁移现有资料后才能更换目录。</p>
            ) : (
              <button
                onClick={() =>
                  void action(async () => {
                    const bridge = (window as any).sufe;
                    if (!bridge?.chooseDirectory) {
                      setNotice("浏览器模式请在上方输入文件夹的绝对路径");
                      return;
                    }
                    const path = await bridge.chooseDirectory();
                    if (path) update("storage_root", path);
                  })
                }
              >
                选择文件夹
              </button>
            )}
            <button
              onClick={() =>
                void action(async () => {
                  await checkStorage();
                })
              }
            >
              检查空间与写入权限
            </button>
            {disk && (
              <p className={disk.sufficient ? "notice" : "alert"}>
                可写入 · 剩余 {(disk.free_bytes / 1024 ** 3).toFixed(1)} GB
                {disk.free_bytes < 5 * 1024 ** 3
                  ? "；图文回放建议至少预留 5 GB 以上。"
                  : ""}
              </p>
            )}
          </section>
        )}
        {step === 2 && (
          <>
            <section>
              <h2>设置自己的学期</h2>
              <div className="form-grid">
                <label>
                  学期名称
                  <input
                    value={draft.term_name}
                    onChange={(e) => update("term_name", e.target.value)}
                  />
                </label>
                <label>
                  第一教学周的周一
                  <input
                    type="date"
                    value={draft.starts_on}
                    onInput={(e) => update("starts_on", e.currentTarget.value)}
                  />
                </label>
                <label>
                  教学周数
                  <input
                    type="number"
                    min={1}
                    max={52}
                    value={draft.teaching_weeks}
                    onChange={(e) =>
                      update("teaching_weeks", Number(e.target.value) || 1)
                    }
                  />
                </label>
              </div>
              <p>已内置上财节次时间，截图中的节次会自动换算，无需配置。</p>
              <button onClick={() => void action(saveTerm)}>保存学期</button>
            </section>
            <section>
              <h2>导入课表 · 可以稍后添加</h2>
              <p>
                手动录入、CSV
                和截图都会先生成草稿。请核对星期、起止时间和单双周，再确认导入。
              </p>
              <div className="toolbar">
                <a href="/timetable-template.csv" download>
                  下载 CSV 模板
                </a>
                <label className="file-button">
                  导入 CSV
                  <input
                    type="file"
                    accept=".csv"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void action(() => importFile(f));
                      e.target.value = "";
                    }}
                  />
                </label>
                <label className="file-button">
                  识别截图
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) void action(() => importFile(f, true));
                      e.target.value = "";
                    }}
                  />
                </label>
              </div>
              {!(settings.settings?.ai?.vision_reuse === false ? settings.settings?.ai?.vision_service?.vision_model : settings.settings?.ai?.vision_model) && (
                <p>
                  截图识别需要看图模型。
                  <button onClick={() => void action(() => go(4))}>
                    先配置 AI，再回来
                  </button>{" "}
                  也可直接手动录入。
                </p>
              )}
              {draft.rows.map((r, i) => (
                <div className="onboarding-row" key={i}>
                  <div className="section-head">
                    <h3>课程 {i + 1}</h3>
                    <button
                      aria-label={`删除课程 ${i + 1}`}
                      onClick={() =>
                        update(
                          "rows",
                          draft.rows.filter((_, j) => j !== i),
                        )
                      }
                    >
                      删除
                    </button>
                  </div>
                  <div className="form-grid">
                    {(
                      [
                        "course_code",
                        "name",
                        "section_code",
                        "start",
                        "end",
                        "weeks",
                        "location",
                      ] as const
                    ).map((k) => (
                      <label key={k}>
                        {
                          {
                            course_code: "课程编号",
                            name: "课程名称",
                            section_code: "教学班",
                            start: "开始时间",
                            end: "结束时间",
                            weeks: "教学周（例如 1,3,5）",
                            location: "地点",
                          }[k]
                        }
                        <input
                          type={k === "start" || k === "end" ? "time" : "text"}
                          value={r[k]}
                          onChange={(e) =>
                            update(
                              "rows",
                              draft.rows.map((v, j) =>
                                j === i ? { ...v, [k]: e.target.value } : v,
                              ),
                            )
                          }
                        />
                      </label>
                    ))}
                    <label>
                      星期
                      <select
                        value={r.weekday}
                        onChange={(e) =>
                          update(
                            "rows",
                            draft.rows.map((v, j) =>
                              j === i
                                ? { ...v, weekday: Number(e.target.value) }
                                : v,
                            ),
                          )
                        }
                      >
                        {[
                          "周一",
                          "周二",
                          "周三",
                          "周四",
                          "周五",
                          "周六",
                          "周日",
                        ].map((d, n) => (
                          <option key={d} value={n}>
                            {d}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="toolbar">
                    {["全周", "单周", "双周"].map((label, n) => (
                      <button
                        key={label}
                        onClick={() =>
                          update(
                            "rows",
                            draft.rows.map((v, j) =>
                              j === i
                                ? {
                                    ...v,
                                    weeks: Array.from(
                                      { length: draft.teaching_weeks },
                                      (_, w) => w + 1,
                                    )
                                      .filter(
                                        (w) =>
                                          n === 0 ||
                                          w % 2 === (n === 1 ? 1 : 0),
                                      )
                                      .join(","),
                                  }
                                : v,
                            ),
                          )
                        }
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <div className="toolbar">
                <button
                  onClick={() => update("rows", [...draft.rows, { ...blank }])}
                >
                  添加课程
                </button>
                <button
                  disabled={!draft.rows.length}
                  onClick={() => void action(preview)}
                >
                  校验并预览
                </button>
                <button
                  disabled={!reviewed}
                  onClick={() =>
                    void action(async () => {
                      const r = await request(
                        "/api/setup/timetable/confirm",
                        payload(),
                      );
                      await refresh();
                      setReviewed(false);
                      setNotice(
                        `已导入，新增 ${r.created_sessions} 节课。重复导入不会重复生成。`,
                      );
                    })
                  }
                >
                  已核对，确认导入
                </button>
              </div>
              {warnings.map((w, i) => (
                <p className="notice" key={i}>
                  {w}
                </p>
              ))}
              <p>已保存 {courses.length} 门课程。未确认的草稿不会自动导入。</p>
            </section>
          </>
        )}
        {step === 3 && (
          <>
            <section>
              <h2>Canvas · 可以稍后连接</h2>
              <p>在独立浏览器窗口登录，完成后关闭窗口，再读取并核对身份。</p>
              <div className="toolbar">
                <button
                  onClick={() =>
                    void action(async () => {
                      await request("/api/connections/canvas/login");
                      setNotice("登录窗口已打开，尚未核验身份");
                    })
                  }
                >
                  登录 Canvas
                </button>
                <button
                  onClick={() =>
                    void action(async () => {
                      await request("/api/connections/canvas/identify");
                      setNotice("正在读取身份，请稍后刷新状态");
                    })
                  }
                >
                  读取 Canvas 身份
                </button>
                <button onClick={() => void action(connections)}>
                  刷新平台状态
                </button>
              </div>
              {identity.user_id && (
                <p>
                  识别到：{identity.name || "未提供姓名"}（{identity.user_id}）
                  <button
                    onClick={() =>
                      void action(async () => {
                        await request("/api/connections/canvas/bind", {
                          user_id: identity.user_id,
                        });
                        await refresh();
                        setNotice("已绑定此账号");
                      })
                    }
                  >
                    确认这是我的账号
                  </button>
                </p>
              )}
              <p>
                {settings.settings?.canvas_user_id
                  ? `已绑定账号 ${settings.settings.canvas_user_id}`
                  : "尚未绑定账号"}{" "}
                · {statusText(platform.canvas)}
              </p>
            </section>
            <section>
              <h2>空中课堂 · 可以稍后连接</h2>
              <p>
                登录后关闭浏览器窗口，再检查登录。当前检查确认可访问“我参与的”页面，不代替姓名核验；请在登录页自行核对账号。
              </p>
              <div className="toolbar">
                <button
                  onClick={() =>
                    void action(async () => {
                      await request("/api/connections/kzkt/login");
                      setNotice("登录窗口已打开，尚未检查登录结果");
                    })
                  }
                >
                  登录空中课堂
                </button>
                <button
                  onClick={() =>
                    void action(async () => {
                      await request("/api/connections/kzkt/check");
                      setNotice("正在检查登录，请稍后刷新平台状态");
                    })
                  }
                >
                  检查空中课堂登录
                </button>
              </div>
              <p>{statusText(platform.kzkt)}</p>
            </section>
            <section>
              <h2>企微属于可选连接</h2>
              <p>
                进入工作台后，在“连接”中选择本机账号、核验本人姓名与组织，再绑定课程群。已缓存附件可归档；未缓存附件需要先在企微手动下载。
              </p>
            </section>
          </>
        )}
        {step === 4 && (
          <>
            <section>
              <h2>选择 AI 使用方式</h2>
              <p>
                不开启
                AI，也可以使用课表、资料同步和平台原文。截图识别需要看图模型。
              </p>
              <AISettings value={ai} onChange={setAi} onSaved={refresh} />
              <button onClick={() => void action(() => go(2))}>返回课表识别</button>
            </section>
            <section>
              <h2>默认回放模式</h2>
              <ReplayMode value={draft.replay_mode} onChange={(mode) => update("replay_mode", mode)} />
              <p>
                可在工作台按课程单独修改。没有平台文字时，可再选择安装 Whisper
                本地转写。
              </p>
              <details>
                <summary>转写与文档转换（以后按需安装）</summary>
                <p>
                  Whisper 组件及模型可能占用数 GB，支持 CPU，兼容 CUDA 时可使用
                  GPU；不会在本向导中自动下载。
                </p>
                <p>
                  PDF 可以直接阅读。PPT、Word 优先使用已安装的
                  Office，也可自行安装{" "}
                  <a
                    href="https://www.libreoffice.org/download/download-libreoffice/"
                    target="_blank"
                    rel="noreferrer"
                  >
                    LibreOffice
                  </a>
                  。缺少转换组件不影响资料归档。
                </p>
              </details>
            </section>
          </>
        )}
        {step === 5 && (
          <>
            <section>
              <h2>
                {settings.configured
                  ? "你的工作台已准备好"
                  : "还需要保存学期设置"}
              </h2>
              <p>
                已保存 {courses.length} 门课程、
                {events.filter(
                  (e) =>
                    e.source === "academic" || e.item_type === "class_session",
                ).length || events.length}{" "}
                条日历安排。
              </p>
              <ul>
                <li>
                  Canvas：
                  {settings.settings?.canvas_user_id
                    ? "已绑定 " + settings.settings.canvas_user_id
                    : "未连接，可稍后配置"}
                </li>
                <li>空中课堂：{statusText(platform.kzkt)}</li>
                <li>
                  文字 AI：
                  {
                    (
                      {
                        none: "未启用",
                        ollama: "本机 Ollama",
                        openai: "自己的兼容 API",
                      } as any
                    )[settings.settings?.ai?.provider || "none"]
                  }
                  ；看图：{(settings.settings?.ai?.vision_reuse === false
                    ? settings.settings?.ai?.vision_service?.vision_model
                    : settings.settings?.ai?.vision_model) || "未配置"}
                </li>
                <li>
                  默认回放：
                  {settings.settings?.replay_mode === "illustrated"
                    ? "图文模式"
                    : "文本模式"}
                </li>
              </ul>
              {!settings.configured && (
                <button onClick={() => void action(() => go(2))}>
                  返回保存学期
                </button>
              )}
              <p>
                未确认的课表草稿不会自动导入。所有可选功能以后都能在设置中开启。
              </p>
            </section>
            <section>
              <h2>首次同步 · 可以稍后进行</h2>
              <p>
                Canvas
                同步当前账号已绑定到本地课表的课程；空中课堂可勾选课程加入队列。启动不代表同步成功，请查看下面的状态或进入“处理任务”。
              </p>
              <button
                disabled={!settings.settings?.canvas_user_id}
                onClick={() =>
                  void action(async () => {
                    await request("/api/academics/canvas/run");
                    setNotice("Canvas 同步已启动，请刷新状态查看结果");
                  })
                }
              >
                启动 Canvas 同步
              </button>
              <p>
                Canvas：{statusText(platform.canvas)}
                {platform.canvas?.error && ` · ${platform.canvas.error}`}
              </p>
              {courses.map((c) => (
                <label className="check" key={c.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(c.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, c.id]
                          : selected.filter((id) => id !== c.id),
                      )
                    }
                  />
                  {c.name}
                </label>
              ))}
              <button
                disabled={!selected.length}
                onClick={() => void action(sync)}
              >
                同步勾选课程的回放
              </button>
              <button onClick={() => void action(connections)}>
                刷新同步状态
              </button>
              {Object.entries(syncResults).map(([id, result]) => (
                <p role="status" key={id}>
                  {courses.find((c) => c.id === id)?.name}：
                  {syncStatus(id, result)}
                </p>
              ))}
              <p>
                空中课堂：{statusText(platform.kzkt)}
                {platform.kzkt?.error && ` · ${platform.kzkt.error}`}
              </p>
            </section>
          </>
        )}
      </fieldset>
      <footer className="onboarding-footer">
        <button
          disabled={busy || step === 0}
          onClick={() => void action(() => go(step - 1))}
        >
          上一步
        </button>
        <span>第 {step + 1} / 6 步</span>
        {step < 5 ? (
          <button
            className="primary"
            disabled={busy}
            onClick={() => void action(next)}
          >
            {busy
              ? "处理中…"
              : step === 2
                ? "保存学期并继续"
                : step === 3
                  ? "继续（连接可跳过）"
                  : step === 4
                    ? "保存学习设置并继续"
                    : "下一步"}
          </button>
        ) : (
          <button
            className="primary"
            disabled={busy || !settings.configured}
            onClick={() =>
              void action(async () => {
                await persist();
                await request("/api/onboarding/finish");
                await onDone();
              })
            }
          >
            进入工作台
          </button>
        )}
      </footer>
    </div>
  );
}
