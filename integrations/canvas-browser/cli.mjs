import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { chromium } from "playwright-core";

const command = process.argv[2] || "status";
const baseUrl = process.env.CANVAS_BASE_URL || "https://canvas.shufe.edu.cn";
const expectedUserId = process.env.CANVAS_EXPECTED_USER_ID || "";
const storageRoot = resolve(process.env.ACADEMIC_STORAGE_ROOT || (() => { throw new Error("Storage directory required"); })());
const apiBase = process.env.PERSONAL_OS_API_BASE_URL || "http://127.0.0.1:8000";
const apiToken = process.env.PERSONAL_OS_TOOL_TOKEN || "";
const chromePath = process.env.CANVAS_CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const runtimeRoot = join(storageRoot, ".runtime", "canvas");
const profileRoot = join(runtimeRoot, "profile");
const downloadRoot = join(runtimeRoot, "downloads");
const statusPath = join(runtimeRoot, "status.json");
const lockPath = join(runtimeRoot, "sync.lock");
const manifestPath = join(runtimeRoot, "download-manifest.json");
const allowedExtensions = new Set([".pdf", ".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".png", ".jpg", ".jpeg", ".gif"]);

if (!new URL(baseUrl).hostname.endsWith("shufe.edu.cn")) throw new Error("Canvas host is not allowed");
if (!existsSync(chromePath)) throw new Error("Google Chrome executable was not found");
mkdirSync(downloadRoot, { recursive: true });

function recordStatus(status, details = {}) {
  let previous = {};
  try { previous = JSON.parse(readFileSync(statusPath, "utf8")); } catch {}
  const now = new Date().toISOString();
  const verified = ["authenticated", "success"].includes(status)
    ? { user_id: details.user_id || null, name: details.name || null, checked_at: now }
    : previous.last_verified || null;
  writeFileSync(statusPath, JSON.stringify({ status, checked_at: now, last_verified: verified, ...details }, null, 2));
}

function canvasFailure(reason, message) {
  const error = new Error(message);
  error.canvas_reason = reason;
  return error;
}

function loginDestination(value) {
  try {
    const url = new URL(value);
    return /\/(?:login|logout|sessions|users\/sign_in)\b/i.test(url.pathname) || /(?:cas|sso|oauth|auth)\b/i.test(url.hostname + url.pathname);
  } catch { return false; }
}

function failureDetails(error) {
  const message = error instanceof Error ? error.message : String(error);
  const reason = error?.canvas_reason || "unknown";
  return { reason, error: message };
}

function safeName(value) {
  return String(value || "resource").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/[. ]+$/g, "").slice(0, 120) || "resource";
}

function loadManifest() {
  try { return JSON.parse(readFileSync(manifestPath, "utf8")); }
  catch { return {}; }
}

function saveManifest(manifest) {
  const temporary = `${manifestPath}.part`;
  writeFileSync(temporary, JSON.stringify(manifest, null, 2));
  renameSync(temporary, manifestPath);
}

async function launch(headless) {
  return chromium.launchPersistentContext(profileRoot, {
    executablePath: chromePath,
    headless,
    acceptDownloads: false,
    viewport: headless ? { width: 1280, height: 900 } : null,
    args: headless ? ["--disable-background-networking"] : ["--start-maximized"],
  });
}

async function canvasJson(page, path) {
  const result = await page.evaluate(async ({ path }) => {
    const response = await fetch(path, { credentials: "same-origin", headers: { accept: "application/json" } });
    const finalUrl = response.url;
    if (!response.ok) return { ok: false, status: response.status, finalUrl, redirected: response.redirected };
    return { ok: true, value: await response.json() };
  }, { path });
  if (result.ok) return result.value;
  if (loginDestination(result.finalUrl) || result.status === 401 || result.status === 403) {
    throw canvasFailure("authentication_required", "Canvas 会话未登录或已失效");
  }
  if (result.status === 404) throw canvasFailure("api_not_found", `Canvas 接口不可用（404）：${path}`);
  if (result.status === 429) throw canvasFailure("rate_limited", "Canvas 暂时限制了请求，请稍后重试");
  if (result.status >= 500) throw canvasFailure("service_unavailable", `Canvas 服务暂时不可用（${result.status}）`);
  throw canvasFailure("request_failed", `Canvas 接口请求失败（${result.status}）：${path}`);
}

async function canvasAll(page, path) {
  const results = [];
  for (let pageNo = 1; pageNo <= 100; pageNo++) {
    const separator = path.includes("?") ? "&" : "?";
    const batch = await canvasJson(page, `${path}${separator}per_page=100&page=${pageNo}`);
    if (!Array.isArray(batch)) throw new Error(`Canvas collection expected for ${path}`);
    results.push(...batch);
    if (batch.length < 100) break;
  }
  return results;
}

async function canvasAllSafe(page, path, label) {
  try { return { items: await canvasAll(page, path), error: null }; }
  catch (error) { return { items: [], error: `${label}: ${error instanceof Error ? error.message : String(error)}` }; }
}

async function identity(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  if (loginDestination(page.url())) throw canvasFailure("authentication_required", "Canvas 会话未登录或已失效");
  const profile = await canvasJson(page, "/api/v1/users/self/profile");
  const userId = String(profile.id || "");
  if (!["identify","login"].includes(command) && userId !== expectedUserId) throw new Error(`Canvas account mismatch: expected ${expectedUserId}`);
  const identity = { user_id: userId, name: profile.name || null };
  if (["identify","login"].includes(command)) writeFileSync(join(runtimeRoot,"identity.json"), JSON.stringify(identity));
  return identity;
}

async function downloadResource(context, courseId, file, manifest) {
  const key = `${courseId}:${file.id}`;
  const modifiedAt = file.updated_at || file.modified_at || null;
  const cached = manifest[key];
  if (cached?.modified_at === modifiedAt && cached?.local_path && existsSync(cached.local_path) && resolve(cached.local_path).startsWith(resolve(downloadRoot))) {
    return { ...cached, source_url: file.url || cached.source_url || null };
  }
  const url = new URL(file.url, baseUrl);
  if (!url.hostname.endsWith("shufe.edu.cn")) throw new Error("Canvas download redirected outside allowed hosts");
  const response = await context.request.get(url.href, { timeout: 120000 });
  if (!response.ok()) throw new Error(`Canvas file download failed: ${response.status()}`);
  const name = safeName(file.filename || file.display_name || `file-${file.id}`);
  const dot = name.lastIndexOf(".");
  const extension = dot >= 0 ? name.slice(dot).toLowerCase() : "";
  if (!allowedExtensions.has(extension)) throw new Error("Canvas file type is not allowed");
  const directory = join(downloadRoot, String(courseId));
  mkdirSync(directory, { recursive: true });
  const temporary = join(directory, `${file.id}-${name}.part`);
  const version = createHash("sha256").update(String(modifiedAt || file.size || "unknown")).digest("hex").slice(0, 10);
  const destination = join(directory, `${file.id}-${version}-${name}`);
  const body = await response.body();
  writeFileSync(temporary, body);
  const digest = createHash("sha256").update(body).digest("hex");
  renameSync(temporary, destination);
  const result = { file_id: String(file.id), title: name, mime_type: file["content-type"] || file.content_type || null,
    byte_size: Number(file.size || body.length), source_url: file.url, modified_at: file.updated_at || file.modified_at || null,
    local_path: destination, sha256: digest };
  manifest[key] = result;
  return result;
}

function htmlText(html) {
  return String(html || "").replace(/<script[\s\S]*?<\/script>/gi, " ").replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<\/?(?:p|div|li|tr|h[1-6])\b[^>]*>/gi, "\n").replace(/<br\s*\/?\s*>/gi, "\n")
    .replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
    .replace(/[ \t]+/g, " ").replace(/\n{3,}/g, "\n\n").trim();
}

async function post(path, payload) {
  const response = await fetch(`${apiBase}${path}`, { method: "POST", headers: { "content-type": "application/json", "x-personal-os-token": apiToken }, body: JSON.stringify(payload) });
  const result = await response.json().catch(() => ({ detail: response.statusText }));
  if (!response.ok) throw new Error(result.detail || `Personal OS ${response.status}`);
  return result;
}

async function sync(page, context, who) {
  const manifest = loadManifest();
  const courses = await canvasAll(page, "/api/v1/courses?enrollment_state=active&include[]=term&include[]=syllabus_body");
  const academicCourses = [];
  const assignments = [];
  for (const course of courses) {
    const courseId = String(course.id);
    const results = await Promise.all([
      canvasAllSafe(page, `/api/v1/courses/${courseId}/files?sort=updated_at&order=desc`, "files"),
      canvasAllSafe(page, `/api/v1/courses/${courseId}/assignments?include[]=submission`, "assignments"),
      canvasAllSafe(page, `/api/v1/courses/${courseId}/discussion_topics?only_announcements=true`, "announcements"),
      canvasAllSafe(page, `/api/v1/courses/${courseId}/modules?include[]=items`, "modules"),
      canvasAllSafe(page, `/api/v1/courses/${courseId}/pages?sort=updated_at&order=desc`, "pages"),
      canvasAllSafe(page, `/api/v1/calendar_events?type=event&context_codes[]=course_${courseId}`, "calendar"),
    ]);
    const [files, courseAssignments, announcements, modules, pageIndex, calendarEvents] = results.map(result => result.items);
    const errors = results.map(result => result.error).filter(Boolean);
    const pages = [];
    for (const item of pageIndex) {
      try {
        const detail = await canvasJson(page, `/api/v1/courses/${courseId}/pages/${encodeURIComponent(item.url)}`);
        pages.push({ url: detail.url, title: detail.title, body: htmlText(detail.body), html_url: detail.html_url, updated_at: detail.updated_at });
      } catch { pages.push({ url: item.url, title: item.title, body: "", html_url: item.html_url, updated_at: item.updated_at }); }
    }
    const resources = [];
    for (const file of files) {
      try { resources.push(await downloadResource(context, courseId, file, manifest)); }
      catch (error) { resources.push({ file_id: String(file.id), title: safeName(file.filename || file.display_name), mime_type: file.content_type || null,
        byte_size: file.size || null, source_url: file.url || null, modified_at: file.updated_at || null }); }
    }
    academicCourses.push({ course_id: courseId, name: course.name || course.course_code || courseId,
      syllabus_text: htmlText(course.syllabus_body), syllabus_url: `${baseUrl}/courses/${courseId}/assignments/syllabus`, syllabus_parsed: {}, resources,
      announcements: announcements.map(item => ({ id: String(item.id), text: htmlText(item.message || item.title), url: item.html_url || null })),
      modules: modules.map(item => ({ id: String(item.id), name: item.name, position: item.position,
        items: (item.items || []).map(entry => ({ id: String(entry.id), title: entry.title, type: entry.type, content_id: entry.content_id || null, html_url: entry.html_url || null })) })),
      pages,
      calendar_events: calendarEvents.map(item => ({ id: String(item.id), title: item.title, start_at: item.start_at, end_at: item.end_at,
        location_name: item.location_name || null, html_url: item.html_url || null })),
      errors });
    for (const item of courseAssignments) {
      const state = item.submission?.workflow_state;
      assignments.push({ course_id: Number(courseId), assignment_id: Number(item.id), course_name: course.name || courseId, title: item.name,
        deadline_at: item.due_at || null, deadline_date: null,
        submission_state: state === "submitted" ? "submitted" : state === "graded" ? "graded" : state === "unsubmitted" ? "unsubmitted" : "unknown",
        description: htmlText(item.description || "") });
    }
  }
  const fetchedAt = new Date().toISOString();
  const academicResult = await post("/api/academics/canvas/sync", { user_id: who.user_id, base_url: baseUrl, fetched_at: fetchedAt, courses: academicCourses });
  const assignmentResult = await post("/api/academics/canvas/assignments", { base_url: baseUrl, user_id: who.user_id, assignments });
  saveManifest(manifest);
  return { courses: academicCourses.length, files: academicCourses.reduce((n, item) => n + item.resources.length, 0), assignments: assignments.length, academicResult, assignmentResult };
}

async function main() {
  if (existsSync(lockPath)) {
    const lockPid = Number.parseInt(readFileSync(lockPath, "utf8").trim(), 10);
    let lockOwnerIsRunning = Number.isInteger(lockPid) && lockPid > 0;
    if (lockOwnerIsRunning) {
      try { process.kill(lockPid, 0); }
      catch { lockOwnerIsRunning = false; }
    }
    if (lockOwnerIsRunning) throw new Error("Canvas browser is already running");
    rmSync(lockPath, { force: true });
  }
  writeFileSync(lockPath, String(process.pid));
  let context;
  try {
    context = await launch(command !== "login");
    const page = context.pages()[0] || await context.newPage();
    if (command === "login") {
      await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
      await page.bringToFront();
      const deadline = Date.now() + 10 * 60 * 1000;
      while (Date.now() < deadline) {
        try { const who = await identity(page); recordStatus("authenticated", who); return; }
        catch { await page.waitForTimeout(3000); }
      }
      throw new Error("Canvas login timed out");
    }
    const who = await identity(page);
    if (["status","identify"].includes(command)) { recordStatus("authenticated", who); console.log(JSON.stringify({ status: "authenticated", ...who })); return; }
    if (command !== "sync") throw new Error(`Unknown command: ${command}`);
    const result = await sync(page, context, who);
    recordStatus("success", { ...who, result });
    console.log(JSON.stringify(result));
  } catch (error) {
    recordStatus("failed", failureDetails(error));
    throw error;
  } finally {
    if (context) await context.close();
    rmSync(lockPath, { force: true });
  }
}

await main();
