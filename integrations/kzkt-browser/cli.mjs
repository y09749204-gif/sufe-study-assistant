import {request as httpRequest} from "node:http";
import {parseSubtitles, replayLinks, assertAuthenticated, episodeDate, transcriptParagraphs} from "./parsers.mjs";
import { createHash } from "node:crypto";
import { createWriteStream, existsSync, mkdirSync, renameSync, rmSync, writeFileSync, statfsSync, appendFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import playwrightCore from "playwright-core";
const { chromium } = playwrightCore;

const command = process.argv[2] || "status";
const baseUrl = process.env.KZKT_BASE_URL || "https://dm.shufe.edu.cn";
const storageRoot = resolve(process.env.ACADEMIC_STORAGE_ROOT || (() => { throw new Error("Storage directory required"); })());
const apiBase = process.env.PERSONAL_OS_API_BASE_URL || "http://127.0.0.1:8000";
const apiToken = process.env.PERSONAL_OS_TOOL_TOKEN || "";
const chromePath = process.env.KZKT_CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const courseHint = process.env.KZKT_COURSE_NAME || "";
const processAfterSync = process.env.KZKT_PROCESS !== "false";
const runtime = join(storageRoot, ".runtime", "kzkt");
const profile = join(runtime, "profile");
const downloadRoot = join(runtime, "downloads");
const discoveryErrors = [];
const statusPath = join(runtime, "status.json");
const lockPath = join(runtime, "sync.lock");

if (new URL(baseUrl).hostname !== "dm.shufe.edu.cn") throw new Error("空中课堂 host 不在白名单内");
if (!existsSync(chromePath)) throw new Error("Google Chrome executable was not found");
mkdirSync(downloadRoot, { recursive: true });

function status(state, details = {}) { writeFileSync(statusPath, JSON.stringify({ status: state, checked_at: new Date().toISOString(), ...details }, null, 2)); }
function allowed(url) { try { return new URL(url, baseUrl).hostname === "dm.shufe.edu.cn"; } catch { return false; } }
function safeName(value) { return String(value || "recording").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").slice(0, 120) || "recording"; }
function text(value) { return String(value || "").replace(/\s+/g, " ").trim(); }

async function open(headless) {
  return chromium.launchPersistentContext(profile, { executablePath: chromePath, headless, acceptDownloads: false,
    viewport: headless ? { width: 1440, height: 1000 } : null, args: headless ? ["--disable-background-networking"] : ["--start-maximized"] });
}

async function post(path, payload) {
  // Processing may include an entire lecture and OCR; fetch's default header
  // timeout can expire while the local API is still producing the result.
  const body = JSON.stringify(payload);
  return new Promise((resolveResult, reject) => {
    const req = httpRequest(new URL(path, apiBase), {method: "POST", headers: {"content-type": "application/json", "content-length": Buffer.byteLength(body), "x-personal-os-token": apiToken}}, response => {
      const chunks = [];
      response.on("data", chunk => chunks.push(chunk));
      response.on("end", () => {
        try { const result = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (response.statusCode >= 400) reject(new Error(result.detail || `Personal OS ${response.statusCode}`)); else resolveResult(result);
        } catch (error) {reject(error);}
      });
      response.on("error", reject);
    });
    req.setTimeout(4 * 3600 * 1000, () => req.destroy(new Error("本地课堂处理超时")));
    req.on("error", reject); req.end(body);
  });
}

async function directMedia(context, url, externalId, title, onProgress) {
  if (command === "discover" || process.env.KZKT_DOWNLOAD_MEDIA === "false") return null;
  if (!url || !allowed(url) || !/\.(mp4|webm|m4v)(?:\?|$)/i.test(url)) return null;
  const extension = new URL(url, baseUrl).pathname.match(/\.(mp4|webm|m4v)$/i)?.[0] || ".mp4";
  const directory = join(downloadRoot, safeName(externalId)); mkdirSync(directory, { recursive: true });
  const temporary = join(directory, `${safeName(title)}${extension}.part`);
  const cookies = await context.cookies(new URL(url, baseUrl).href);
  const cookieHeader = cookies.map(cookie => `${cookie.name}=${cookie.value}`).join("; ");
  const headers = cookieHeader ? {cookie:cookieHeader} : {};
  const head = await fetch(new URL(url,baseUrl).href,{method:"HEAD",redirect:"error",headers,signal:AbortSignal.timeout(60000)});
  if (!head.ok) throw new Error(`回放下载失败：${head.status}`);
  const totalBytes = Number(head.headers.get("content-length") || 0);
  const disk = statfsSync(storageRoot);
  if (disk.bavail * disk.bsize < totalBytes + 5 * 1024 ** 3) throw new Error("存储空间不足：保留至少 5 GB 后无法下载此回放");
  const hash = createHash("sha256");
  let downloadedBytes = 0;
  writeFileSync(temporary, Buffer.alloc(0));
  try {
    if (totalBytes > 0 && head.headers.get("accept-ranges") === "bytes") {
      // Commit only complete bounded blocks. A interrupted HTTP body never
      // corrupts the partial file/hash, and large lectures avoid long streams.
      while (downloadedBytes < totalBytes) {
        const end = Math.min(totalBytes - 1, downloadedBytes + 32 * 1024 ** 2 - 1);
        let data;
        for (let attempt=0; attempt<3; attempt++) {
          try {
            const response = await fetch(new URL(url,baseUrl).href,{redirect:"error",headers:{...headers,Range:`bytes=${downloadedBytes}-${end}`},signal:AbortSignal.timeout(90000)});
            if (response.status!==206 || response.headers.get("content-range")!==`bytes ${downloadedBytes}-${end}/${totalBytes}`) { await response.body?.cancel(); throw new Error("媒体分段响应不一致"); }
            data=Buffer.from(await response.arrayBuffer());
            if (data.length!==end-downloadedBytes+1) throw new Error("媒体分段不完整");
            break;
          } catch(error) { if(attempt===2)throw error; await new Promise(resolve=>setTimeout(resolve,1000)); }
        }
        appendFileSync(temporary,data);hash.update(data);downloadedBytes+=data.length;
        onProgress?.({downloadedBytes,totalBytes});
      }
    } else {
      const response=await fetch(new URL(url,baseUrl).href,{redirect:"error",headers});
      if(!response.ok || !response.body)throw new Error(`回放下载失败：${response.status}`);
      await pipeline(Readable.fromWeb(response.body),async function*(source){for await(const chunk of source){hash.update(chunk);downloadedBytes+=chunk.length;onProgress?.({downloadedBytes,totalBytes});yield chunk;}},createWriteStream(temporary));
      if(totalBytes && downloadedBytes!==totalBytes)throw new Error("媒体下载长度不一致");
    }
  } catch(error) {rmSync(temporary,{force:true});throw error;}
  const digest = hash.digest("hex");
  const destination = join(directory, `${digest.slice(0, 12)}-${safeName(title)}${extension}`);
  if (existsSync(destination)) rmSync(temporary, { force: true }); else renameSync(temporary, destination);
  onProgress?.({ downloadedBytes, totalBytes, complete: true });
  return { local_path: destination, media_sha256: digest };
}

async function discover(page, context) {
  await page.goto(`${baseUrl.replace(/\/$/, "")}/learn/videoreview`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(1500);
  assertAuthenticated(await page.locator("body").innerText(), await page.getByRole("link", {name: "登录", exact: true}).count() > 0);
  const mine = page.getByText("我参与的", {exact: true});
  if (await mine.count() !== 1) throw new Error("page_changed: 无法唯一定位我参与的筛选");
  await mine.click(); await page.waitForTimeout(1000);
  const all = [], signatures = new Set();
  while (true) {
    const entries = await page.locator("a[href]").evaluateAll(nodes => nodes.map(node => ({href: node.href, label: (node.textContent || "").trim(), outer: (node.closest("article,li,div")?.textContent || "").trim()})));
    const links = replayLinks(entries, baseUrl);
    const signature = links.map(x => x.href).join("|");
    if (signatures.has(signature)) throw new Error("page_changed: 分页未前进，不能报告完整同步");
    signatures.add(signature); all.push(...links);
    if (!links.length && !(await page.locator("body").innerText()).includes("暂无数据")) throw new Error("page_changed: 未找到可识别的回放列表");
    const next = page.locator('.el-pagination .btn-next, .ant-pagination-next, a[rel="next"], button[aria-label="下一页"]');
    if (!await next.count()) break;
    if (await next.count() !== 1) throw new Error("page_changed: 分页控件不唯一");
    if (await next.isDisabled() || await next.getAttribute("aria-disabled") === "true" || /disabled/.test(await next.getAttribute("class") || "")) break;
    await next.click(); await page.waitForTimeout(1000);
  }
  const seen = new Set(); const recordings = [];
  const selectedPages = replayLinks(all, baseUrl).filter(item => !courseHint || item.outer.includes(courseHint) || item.label.includes(courseHint));
  status("syncing", { message: "正在检查可访问回放", progress: { stage: "discovering", percent: 5, total: selectedPages.length } });
  for (const [index, item] of selectedPages.entries()) {
    if (seen.has(item.href)) continue; seen.add(item.href);
    status("syncing", { message: `正在读取第 ${index + 1}/${selectedPages.length} 条回放`, progress: { stage: "reading", percent: 5 + Math.round(15 * index / Math.max(selectedPages.length, 1)), current: index + 1, total: selectedPages.length, title: item.label } });
    const detail = await page.context().newPage();
    try {
      await detail.goto(item.href, { waitUntil: "domcontentloaded", timeout: 60000 }); await detail.waitForTimeout(500);
      if (await detail.getByRole("link", {name: "登录", exact: true}).count()) throw new Error("login_required: 回放读取时登录失效");
      let match = await post("/api/academics/kzkt/match", {course_name: item.label});
      if (!match.course_id || (courseHint && match.course_name !== courseHint)) continue;
      const courseResponse = await fetch(`${apiBase}/api/academics/courses/${match.course_id}`);
      if (!courseResponse.ok) throw new Error("无法读取本地课表用于核对课次");
      const course = await courseResponse.json();
      // The verified platform uses a course page with a week/lesson menu, not
      // one URL per recording. Expand only completed teaching weeks.
      const weeks = detail.locator(".el-submenu:not(.is-disabled)");
      await weeks.first().waitFor({state: "visible", timeout: 30000});
      for (let i = 0; i < await weeks.count(); i++) {
        if (await weeks.nth(i).getAttribute("aria-expanded") !== "true") await weeks.nth(i).locator(".el-submenu__title").dispatchEvent("click");
      }
      const lessons = detail.locator(".el-menu-item:not(.is-disabled)").filter({has: detail.locator(".section_name_date")});
      await lessons.first().waitFor({state: "attached", timeout: 15000});
      const descriptors = await lessons.evaluateAll(nodes => nodes.map(node => ({
        date: node.querySelector(".section_name_date")?.textContent?.trim(),
        period: node.querySelector(".section_name_section")?.textContent?.replace(/\s+/g, " ").trim(),
        week: node.closest(".el-submenu")?.querySelector(".date")?.textContent?.trim()
      })));
      if (!descriptors.length) throw new Error("page_changed: 已参与课程未找到可识别的课次目录");
      for (const [lessonIndex, descriptor] of descriptors.entries()) {
        const day = episodeDate(descriptor.week, descriptor.date);
        const externalId = createHash("sha256").update(`${item.href}|${day}|${descriptor.period}`).digest("hex").slice(0, 32);
        if (process.env.KZKT_RECORDING_ID && process.env.KZKT_RECORDING_ID !== externalId) continue;
        const sameDay = course.sessions.filter(row => new Date(row.start_at).toLocaleDateString("en-CA", {timeZone: "Asia/Shanghai"}) === day);
        const taughtAt = sameDay.length === 1 ? sameDay[0].start_at : null;
        if (!(await lessons.nth(lessonIndex).getAttribute("class") || "").includes("is-active")) {
          await lessons.nth(lessonIndex).dispatchEvent("click"); await detail.waitForTimeout(1000);
        }
        await detail.waitForFunction(() => [...document.querySelectorAll('video')].some(video => video.readyState >= 1), null, {timeout: 15000});
        const playbackBefore = await detail.locator("video").evaluateAll(nodes => nodes.map(video => ({paused: video.paused, time: video.currentTime})));
        if (playbackBefore.some(video => !video.paused)) throw new Error("播放器正在播放，已停止采集");
        const media = await detail.locator(".video_container video").evaluateAll(nodes => nodes.map(node => ({url: node.src || node.currentSrc, duration: node.duration, width: node.videoWidth})).filter(node => node.url));
        // Verified player containers: room camera first, presentation second.
        // Ignore the unrelated hidden note-editor video and metadata readiness.
        if (media.length > 2) throw new Error("page_changed: 无法确定多通道中的课件画面");
        const presentation = media.at(-1);
        const tracks = await detail.locator("track[kind='subtitles'],track[kind='captions']").evaluateAll(nodes => nodes.map(node => node.src).filter(Boolean));
        let subtitleText = null; let subtitleSegments = [];
        if (tracks[0] && allowed(tracks[0])) {
          const response = await context.request.get(tracks[0], {timeout: 30000, maxRedirects: 0});
          if (response.ok()) { const parsed = parseSubtitles(await response.text()); subtitleText = parsed.text; subtitleSegments = parsed.segments; }
        }
        if (!subtitleText && command !== "discover") {
          const speechTab = detail.locator(".tab-item").filter({hasText: /^语音文本$/});
          if (await speechTab.count() === 1) {
            await speechTab.click(); await detail.waitForTimeout(500);
            subtitleSegments = await detail.locator(".paragraph").evaluateAll(transcriptParagraphs);
            // The speech panel supplies starts, not the final cue's end. Player
            // duration can be Infinity while loading; never let it version the transcript.
            subtitleSegments.forEach((segment, i) => {segment.end = subtitleSegments[i + 1]?.start ?? null;});
            subtitleText = subtitleSegments.map(row => row.text).join("\n") || null;
          }
        }
        const title = `${match.course_name} ${day} ${descriptor.period}`;
        const downloaded = process.env.KZKT_MEDIA_MODE === "text" ? null : await directMedia(context, presentation?.url, externalId, title, ({downloadedBytes, totalBytes, complete}) => {
          status("syncing", {message: complete ? `已下载：${title}` : `正在下载：${title}`,
            progress: {stage: "downloading", percent: totalBytes ? Math.min(70, 20 + Math.round(50 * downloadedBytes / totalBytes)) : 20,
              current: lessonIndex + 1, total: descriptors.length, title, downloaded_bytes: downloadedBytes, total_bytes: totalBytes}});
        });
        const playbackAfter = await detail.locator("video").evaluateAll(nodes => nodes.map(video => ({paused: video.paused, time: video.currentTime})));
        const paused = playbackAfter.length === playbackBefore.length && playbackAfter.every((video, i) => video.paused && Math.abs(video.time - playbackBefore[i].time) < .25);
        if (!paused) throw new Error(`播放器状态发生变化，已停止采集：${JSON.stringify({before: playbackBefore, after: playbackAfter})}`);
        recordings.push({external_id: externalId, course_external_id: new URL(item.href).searchParams.get("id") || null,
          course_name: match.course_name, section_code: match.section_code, title, source_url: item.href, taught_at: taughtAt, published_at: null, platform_session_id: null,
          participation_evidence: {filter: "我参与的", checked_at: new Date().toISOString(), platform_date: day, platform_period: descriptor.period,
            taught_time_basis: taughtAt ? "platform_date_and_unique_timetable_session" : "unresolved", media_role: "presentation", playback_unchanged: paused},
          local_path: downloaded?.local_path || null, media_sha256: downloaded?.media_sha256 || null, subtitle_text: subtitleText, subtitle_segments: subtitleSegments,
          ...(command === "discover" ? {session_id: sameDay.length === 1 ? sameDay[0].id : null, media_available: !!presentation?.url && allowed(presentation.url) && /\.(mp4|webm|m4v)(?:\?|$)/i.test(presentation.url)} : {})});
      }
    } catch (error) {
      if (String(error).includes("login_required")) throw error;
      discoveryErrors.push({source_url: item.href, error: String(error)});
    } finally { await detail.close(); }
  }
  return recordings;
}

async function main() {
  if (existsSync(lockPath)) {
    const priorPid = Number.parseInt(String(await import("node:fs").then(({ readFileSync }) => readFileSync(lockPath, "utf8"))).trim(), 10);
    let alive = false;
    try { if (Number.isInteger(priorPid) && priorPid > 0) process.kill(priorPid, 0), alive = true; } catch {}
    if (alive) throw new Error("空中课堂浏览器正在运行");
    rmSync(lockPath, { force: true });
  }
  writeFileSync(lockPath, String(process.pid), {flag: "wx"}); let context;
  try {
    context = await open(command !== "login");
    if (command !== "login") await context.addInitScript(() => {
      HTMLMediaElement.prototype.play = function () { this.pause(); return Promise.resolve(); };
      document.addEventListener("play", event => { if (event.target instanceof HTMLMediaElement) event.target.pause(); }, true);
      const stopAutoplay = () => document.querySelectorAll("video,audio").forEach(media => {media.autoplay = false; media.removeAttribute("autoplay"); if (!media.paused) media.pause();});
      new MutationObserver(stopAutoplay).observe(document, {childList:true,subtree:true});
      document.addEventListener("DOMContentLoaded", stopAutoplay);
    });
    const page = context.pages()[0] || await context.newPage();
    if (command === "login") {
      await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 60000 }); await page.bringToFront();
      await page.waitForTimeout(10 * 60 * 1000); status("login_window_closed", { message: "请关闭登录窗口后开始同步" }); return;
    }
    if (command === "status") { await page.goto(`${baseUrl}/learn/videoreview`, {waitUntil: "networkidle"}); assertAuthenticated(await page.locator("body").innerText(), await page.getByRole("link", {name: "登录", exact: true}).count() > 0); status("authenticated"); console.log(JSON.stringify({status: "authenticated"})); return; }
    if (!["sync", "discover"].includes(command)) throw new Error(`Unknown command: ${command}`);
    status("syncing", { message: "正在发现本人可访问的回放", progress: { stage: "discovering", percent: 2 } });
    const recordings = await discover(page, context);
    if (command === "discover") { console.log(JSON.stringify({recordings: recordings.map(({subtitle_text, subtitle_segments, ...item}) => item), errors: discoveryErrors})); status("discovered", {message: "仅发现回放，尚未下载或处理"}); return; }
    status("syncing", { message: "正在写入回放索引", progress: { stage: "ingesting", percent: 75, total: recordings.length } });
    const result = await post("/api/academics/kzkt/ingest", { base_url: baseUrl, fetched_at: new Date().toISOString(), recordings });
    status("syncing", { message: "正在生成课堂文本与复盘", progress: { stage: "reviewing", percent: 82, total: recordings.length } });
    const batches = [];
    if (processAfterSync) {
      for (const recordingId of result.recording_ids || []) {
        batches.push(await post(`/api/academics/kzkt/process?recording_id=${encodeURIComponent(recordingId)}`, {}));
      }
    }
    const failedCount = discoveryErrors.length + batches.reduce((sum, item) => sum + item.failed_count, 0);
    const state = failedCount ? "partial_failure" : recordings.length ? "success" : "no_recordings";
    const report = {discovered: recordings.length, ...result, processed: batches, errors: discoveryErrors, failed_count: failedCount};
    status(state, {message: state === "no_recordings" ? "已登录，本人参与课程中暂无匹配回放" : failedCount ? "部分处理失败，请重试" : "同步完成", result: report});
    console.log(JSON.stringify(report));
    if (failedCount) process.exitCode = 1;
  } catch (error) { status(String(error).includes("login_required") ? "login_required" : "failed", { error: error instanceof Error ? error.message : String(error) }); throw error; }
  finally { if (context) await context.close(); rmSync(lockPath, { force: true }); }
}
await main();
