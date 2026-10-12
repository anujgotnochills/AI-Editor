/**
 * panel.js — StyleCut Panel Controller
 * =====================================
 * Thin orchestration layer connecting Premiere Pro's timeline to
 * the local StyleCut Python engine (127.0.0.1:7820).
 *
 * Flow:
 *   1. Reads active sequence media path via ExtendScript (host.jsx)
 *   2. Sends job to POST 127.0.0.1:7820/jobs/captions
 *   3. Polls GET 127.0.0.1:7820/jobs/{id}/progress (updates progress bar)
 *   4. Fetches resulting SRT path from GET 127.0.0.1:7820/jobs/{id}/result
 *   5. Calls ExtendScript SC_importSRT(path, lang) to insert into timeline
 */

(function () {
    "use strict";

    /* ── Constants ─────────────────────────────────────────────────────────── */
    const ENGINE_BASE = "http://127.0.0.1:7820";
    const PANEL_VERSION = "0.2.1";
    const DEMO_SRT_FILENAME = "demo_captions.srt";

    /* ── State ─────────────────────────────────────────────────────────────── */
    let currentMediaPath = "";
    let currentTimelineClips = [];
    let isProcessing = false;
    let activeManifestPath = "";
    let activeCaptionLines = [];

    /* ── CSInterface init ──────────────────────────────────────────────────── */
    const cs = new CSInterface();
    const env = cs.hostEnvironment || (typeof cs.getHostEnvironment === "function" ? cs.getHostEnvironment() : null) || {};
    const appName = env.appName || "PPRO";
    const appVersion = env.appVersion || "";

    /* ── UI refs ───────────────────────────────────────────────────────────── */
    const $ = id => document.getElementById(id);
    const btnInfo       = $("btn-info");
    const btnGenerate   = $("btn-generate");
    const btnDemo       = $("btn-demo");
    const btnPing       = $("btn-ping");
    const formatToggle  = $("format-toggle");
    const templateToggle = $("template-toggle");
    const langToggle    = $("lang-toggle");
    const linesToggle   = $("lines-toggle");
    const wordsToggle   = $("words-toggle");
    const speedToggle   = $("speed-toggle");
    const statusBox     = $("status");
    const logArea       = $("log-area");
    const chipName      = $("chip-name");
    const chipMedia     = $("chip-media");
    const progressCont  = $("progress-container");
    const progressFill  = $("progress-fill");
    const progressStep  = $("progress-step");
    const progressPct   = $("progress-pct");
    const editorCard    = $("caption-editor-card");
    const editorList    = $("caption-editor-list");
    const editorLineCount = $("editor-line-count");
    const btnRefreshCaptions = $("btn-refresh-captions");

    /* ── Logging & Status ──────────────────────────────────────────────────── */
    function log(msg, level = "info") {
        const ts = new Date().toLocaleTimeString();
        const line = `[${ts}] [${level.toUpperCase()}] ${msg}`;
        logArea.value = line + "\n" + logArea.value;
        console.log(line);
    }

    function setStatus(msg, type = "idle") {
        statusBox.textContent = msg;
        statusBox.className = "status-box " + type;
    }

    function setProgress(pct, step) {
        progressCont.classList.add("active");
        progressFill.style.width = pct + "%";
        progressPct.textContent = pct + "%";
        progressStep.textContent = step;
    }

    function hideProgress() {
        progressCont.classList.remove("active");
        progressFill.style.width = "0%";
    }

    /* ── ExtendScript bridge ───────────────────────────────────────────────── */
    function evalScript(fn, ...args) {
        return new Promise((resolve, reject) => {
            const extPath = cs.getSystemPath(SystemPath.EXTENSION).replace(/\\/g, "/");
            const hostPath = extPath + "/jsx/host.jsx";
            const argStr = args.map(a => JSON.stringify(a)).join(", ");
            const call = `(function(){ try { $.evalFile("${hostPath}"); } catch(e){} return ${fn}(${argStr}); })()`;
            cs.evalScript(call, result => {
                if (result === "EvalScript error.") {
                    cs.evalScript(`${fn}(${argStr})`, fbResult => {
                        try { resolve(JSON.parse(fbResult)); }
                        catch (e) { resolve({ ok: false, raw: fbResult }); }
                    });
                    return;
                }
                try { resolve(JSON.parse(result)); }
                catch (e) { resolve({ ok: false, raw: result }); }
            });
        });
    }

    /* ── HTTP Helpers (Fetch with Node.js fallback) ────────────────────────── */
    async function apiGet(endpoint) {
        const url = `${ENGINE_BASE}${endpoint}`;
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (fetchErr) {
            if (typeof require === "function") {
                return new Promise((resolve, reject) => {
                    try {
                        const http = require("http");
                        http.get(url, (res) => {
                            let data = "";
                            res.on("data", chunk => data += chunk);
                            res.on("end", () => {
                                try { resolve(JSON.parse(data)); }
                                catch (e) { reject(e); }
                            });
                        }).on("error", reject);
                    } catch (e) {
                        reject(fetchErr);
                    }
                });
            }
            throw fetchErr;
        }
    }

    async function apiPost(endpoint, bodyData) {
        const url = `${ENGINE_BASE}${endpoint}`;
        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(bodyData)
            });
            if (!res.ok) {
                const errJson = await res.json().catch(() => ({}));
                throw new Error(errJson.detail || `HTTP ${res.status}`);
            }
            return await res.json();
        } catch (fetchErr) {
            if (typeof require === "function") {
                return new Promise((resolve, reject) => {
                    try {
                        const http = require("http");
                        const postData = JSON.stringify(bodyData);
                        const u = new URL(url);
                        const opts = {
                            hostname: u.hostname,
                            port: u.port,
                            path: u.pathname,
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "Content-Length": Buffer.byteLength(postData)
                            }
                        };
                        const req = http.request(opts, (res) => {
                            let data = "";
                            res.on("data", chunk => data += chunk);
                            res.on("end", () => {
                                try { resolve(JSON.parse(data)); }
                                catch (e) { reject(e); }
                            });
                        });
                        req.on("error", reject);
                        req.write(postData);
                        req.end();
                    } catch (e) {
                        reject(fetchErr);
                    }
                });
            }
            throw fetchErr;
        }
    }

    /* ── Engine Health Check ───────────────────────────────────────────────── */
    async function pingEngine(silent = false) {
        try {
            const data = await apiGet("/health");
            if (!silent) {
                const aiStatus = data.gemini_configured ? " | Gemini AI: Active ✓" : " | Gemini AI: Key Missing";
                log(`Engine online — v${data.version} (${(data.device || "CPU").toUpperCase()})${aiStatus}`, "info");
            }
            setStatus(`Engine online ✓ (${(data.device || "CPU").toUpperCase()})`, "ok");
            return true;
        } catch (e) {
            if (!silent) {
                log("Engine connection failed: " + e.message, "warn");
            }
            setStatus("Engine offline — start engine.exe", "warn");
            return false;
        }
    }

    /* ── Sequence Media Detection ─────────────────────────────────────────── */
    async function detectSequenceMedia() {
        log("Detecting active sequence media…");
        setStatus("Reading sequence…", "busy");
        try {
            const info = await evalScript("SC_getActiveSequenceInfo");
            if (!info.ok) throw new Error(info.error || info.raw || "No active sequence found");

            const durText = (info.duration > 0) ? ` (${info.duration.toFixed(1)}s)` : "";
            chipName.textContent = "Seq: " + (info.name || "Untitled") + durText;
            currentTimelineClips = info.timelineClips || [];

            if (info.mediaPath) {
                currentMediaPath = info.mediaPath;
                const filename = info.mediaPath.split("\\").pop().split("/").pop();
                chipMedia.textContent = "Media: " + filename;
                chipMedia.title = info.mediaPath;
                if (currentTimelineClips.length > 0) {
                    const c0 = currentTimelineClips[0];
                    log(`Found media: ${filename} (${currentTimelineClips.length} clip(s) on timeline, In: ${c0.in_point.toFixed(2)}s, Out: ${c0.out_point.toFixed(2)}s, Dur: ${c0.duration.toFixed(2)}s, Start: ${c0.start.toFixed(2)}s)`);
                } else {
                    log(`Found media: ${filename}`);
                }
                setStatus(`Ready: ${filename}`, "ok");
            } else {
                currentMediaPath = "";
                chipMedia.textContent = "Media: (None found)";
                log("No media file detected on sequence tracks.", "warn");
                setStatus("No media on sequence", "warn");
            }
            return info;
        } catch (e) {
            log(e.message, "error");
            setStatus("Sequence read error", "error");
            return null;
        }
    }

    /* ── AI Caption Flow (Step 3) ──────────────────────────────────────────── */
    async function generateAndInsertCaptions() {
        if (isProcessing) return;

        // 1. Verify engine is online
        const isOnline = await pingEngine(true);
        if (!isOnline) {
            log("Cannot start: StyleCut engine is offline. Start 'python engine.py'", "error");
            setStatus("Engine offline — start engine.exe", "error");
            return;
        }

        // 2. Always refresh sequence media to get the latest cuts & in/out trim points
        const info = await detectSequenceMedia();
        if (!info || (!info.mediaPath && currentTimelineClips.length === 0)) {
            log("Please open a sequence containing a video or audio clip first.", "error");
            setStatus("No active media found", "error");
            return;
        }

        isProcessing = true;
        btnGenerate.disabled = true;
        btnGenerate.style.opacity = "0.6";

        const lang = langToggle.value; // "hinglish", "en", "hi", "auto"
        const formatMode = formatToggle ? formatToggle.value : "graphics";
        const templateName = templateToggle ? templateToggle.value : "hook";
        const maxLines = linesToggle ? (parseInt(linesToggle.value, 10) || 1) : 1;
        const wordsPerLine = parseInt(wordsToggle.value, 10) || 3;
        const modelSpeed = speedToggle ? speedToggle.value : "base";

        let whisperLang = null;
        let hinglish = false;
        if (lang === "en") {
            whisperLang = "en";
            hinglish = false;
        } else if (lang === "hi") {
            whisperLang = "hi";
            hinglish = false;
        } else if (lang === "hinglish") {
            whisperLang = null;
            hinglish = true;
        } else if (lang === "auto") {
            whisperLang = null;
            hinglish = false;
        }

        // 3. Directly export active sequence timeline audio mix from Premiere!
        // This renders the EXACT audio playing on the timeline (cuts, trims, and arrangements).
        let audioSourcePath = currentMediaPath;
        let isTimelineMix = false;

        log("Rendering active sequence timeline audio mix…");
        setStatus("Exporting timeline audio…", "busy");
        setProgress(7, "Exporting sequence timeline audio…");

        try {
            const extPath = cs.getSystemPath(SystemPath.EXTENSION).replace(/\\/g, "/");
            const presetPath = extPath + "/presets/stylecut-audio.epr";
            const tempWav = "C:/Users/ANUJ/AppData/Local/Temp/stylecut_timeline_" + Date.now() + ".wav";

            const exportRes = await evalScript("SC_exportTimelineAudio", tempWav, presetPath);
            if (exportRes && exportRes.ok && exportRes.path) {
                audioSourcePath = exportRes.path;
                isTimelineMix = true;
                const durStr = (exportRes.duration && exportRes.duration > 0) ? `${exportRes.duration.toFixed(1)}s` : `${info.duration ? info.duration.toFixed(1) + 's' : ''}`;
                log(`✅ Sequence timeline audio exported (${durStr}, ${(exportRes.size / 1024).toFixed(0)} KB): ${exportRes.path}`);
            } else {
                log(`Direct timeline export unavailable (${exportRes ? (exportRes.error || JSON.stringify(exportRes)) : 'null'}). Using clip detection fallback.`, "warn");
            }
        } catch (eExp) {
            log(`Direct timeline export error: ${eExp.message}. Falling back to clip detection.`, "warn");
        }

        log(`Starting caption pipeline for: ${audioSourcePath} (${isTimelineMix ? 'Sequence Timeline Mix' : 'Raw Clip Source'})`);
        log(`Options: format=${formatMode}, style=${templateName}, language=${lang} (whisper=${whisperLang || 'auto'}, hinglish=${hinglish}), lines=${maxLines}, words_per_line=${wordsPerLine}, speed=${modelSpeed}`);
        setStatus("Submitting job…", "busy");
        setProgress(12, "Submitting job to engine…");

        try {
            // 4. Submit job to FastAPI engine
            const payload = {
                media_path: audioSourcePath,
                is_timeline_audio: isTimelineMix,
                sequence_duration: (info && (info.duration || info.sequenceDuration)) ? (info.duration || info.sequenceDuration) : 0,
                timeline_clips: currentTimelineClips,
                language: whisperLang,
                hinglish: hinglish,
                words_per_line: wordsPerLine,
                max_lines: maxLines,
                template: templateName,
                format: formatMode,
                model: modelSpeed,
                sequence_width: (info && info.frameWidth) ? info.frameWidth : 1080,
                sequence_height: (info && info.frameHeight) ? info.frameHeight : 1920
            };

            const postRes = await apiPost("/jobs/captions", payload);
            const job_id = postRes.job_id;
            log(`Job started [ID: ${job_id}]`);

            // 4. Poll job progress
            let jobDone = false;
            let resultData = null;

            while (!jobDone) {
                await new Promise(r => setTimeout(r, 600));

                const prog = await apiGet(`/jobs/${job_id}/progress`);
                setProgress(prog.pct, prog.step);

                if (prog.status === "completed") {
                    jobDone = true;
                    resultData = await apiGet(`/jobs/${job_id}/result`);
                } else if (prog.status === "failed") {
                    throw new Error(prog.error || "Caption generation failed");
                }
            }

            // 5. Place captions onto Premiere Pro timeline!
            if (formatMode === "graphics" && resultData.manifest_path) {
                log(`🎬 Placing Remotion template clips from manifest on Video Track 2...`);
                setProgress(95, "Placing per-line caption clips on Video Track V2…");
                const manifestRes = await evalScript("SC_importCaptionsManifest", resultData.manifest_path);
                if (manifestRes && manifestRes.ok) {
                    setProgress(100, "Finished!");
                    log(`🎉 ${manifestRes.count || 'All'} editable per-line caption clips placed on ${manifestRes.track || 'Video Track 2'}!`);
                    log(`⚡ Template: ${manifestRes.template || templateName} | Clustered in 'StyleCut Captions' Bin`);
                    setStatus("Editable Captions on V2 ✓", "ok");
                    await loadCaptionLines(resultData.manifest_path);
                } else {
                    log(`Manifest placement fallback: ${manifestRes ? manifestRes.error : 'Unknown error'}. Trying single overlay...`, "warn");
                    if (resultData.overlay_mov) {
                        const overlayRes = await evalScript("SC_importGraphicOverlay", resultData.overlay_mov);
                        if (overlayRes && overlayRes.ok) {
                            setProgress(100, "Finished!");
                            log(`🎉 Captions placed directly on ${overlayRes.track || 'Video Track 2'} as a transparent graphic asset!`);
                            setStatus("Graphic Captions on V2 ✓", "ok");
                        }
                    }
                }
            } else if (formatMode === "graphics" && resultData.overlay_mov) {
                log(`🎨 Placing transparent graphic captions overlay on Video Track: ${resultData.overlay_mov}`);
                setProgress(95, "Placing graphic overlay on Video Track V2…");
                const overlayRes = await evalScript("SC_importGraphicOverlay", resultData.overlay_mov);
                if (!overlayRes.ok) {
                    log(`Graphic overlay placement fallback: ${overlayRes.error}. Placing on caption track…`, "warn");
                    const srtPath = (lang === "hinglish") ? resultData.srt_hi : resultData.srt_en;
                    const insertResult = await evalScript("SC_importSRT", srtPath, lang, "subtitles", templateName);
                    setProgress(100, "Finished!");
                    log(`✅ Captions placed on timeline (${insertResult.label})!`);
                    setStatus("Captions on timeline ✓", "ok");
                } else {
                    setProgress(100, "Finished!");
                    log(`🎉 Captions placed directly on ${overlayRes.track || 'Video Track 2'} as a transparent graphic asset!`);
                    log(`⚡ 100% Automated: Exact Hook Typography, Golden Yellow focus keyword & soft drop shadow applied!`);
                    setStatus("Graphic Captions on V2 ✓", "ok");
                }
            } else {
                const srtPath = (lang === "hinglish") ? resultData.srt_hi : resultData.srt_en;
                if (!srtPath) throw new Error("Engine did not return an SRT path");

                log(`Captions generated (${resultData.words_count} words). Placing onto Premiere subtitle track…`);
                setProgress(95, `Placing captions on timeline…`);

                const insertResult = await evalScript("SC_importSRT", srtPath, lang, formatMode, templateName);
                if (!insertResult.ok) {
                    throw new Error(insertResult.error || "Failed to place caption track on sequence");
                }

                setProgress(100, "Finished!");
                log(`✅ Captions placed on subtitle track (${insertResult.label})!`);
                setStatus("Captions on timeline ✓", "ok");
            }

            setTimeout(hideProgress, 3500);

        } catch (err) {
            log(err.message, "error");
            setStatus("Error — see log", "error");
            hideProgress();
        } finally {
            isProcessing = false;
            btnGenerate.disabled = false;
            btnGenerate.style.opacity = "1";
        }
    }

    /* ── Demo SRT / Graphic Fallback ────────────────────────────────────────── */
    function getDemoSrtPath() {
        const extPath = cs.getSystemPath(SystemPath.EXTENSION);
        return extPath + "\\" + DEMO_SRT_FILENAME;
    }

    btnDemo.addEventListener("click", async () => {
        const lang = langToggle.value;
        const formatMode = formatToggle ? formatToggle.value : "graphics";
        const templateName = templateToggle ? templateToggle.value : "hook";
        const extPath = cs.getSystemPath(SystemPath.EXTENSION);
        const demoMovPath = extPath + "\\demo_captions_overlay.mov";
        const srtPath = getDemoSrtPath();

        if (formatMode === "graphics") {
            log(`Importing demo graphic overlay (${templateName}) onto Video Track 2...`);
            setStatus("Placing demo graphic…", "busy");
            try {
                const result = await evalScript("SC_importGraphicOverlay", demoMovPath);
                if (result.ok) {
                    log(`🎉 Demo graphic overlay placed on ${result.track || 'Video Track 2'}!`);
                    setStatus("Demo Graphic inserted ✓", "ok");
                    return;
                } else {
                    log(`Graphic overlay notice: ${result.error}. Falling back to demo SRT.`, "warn");
                }
            } catch (eg) {
                log(`Graphic demo error: ${eg.message}. Falling back to demo SRT.`, "warn");
            }
        }

        log(`Importing demo SRT (${lang}, format=${formatMode}, style=${templateName}) from: ${srtPath}`);
        setStatus("Importing demo captions…", "busy");
        try {
            const result = await evalScript("SC_importSRT", srtPath, lang, formatMode, templateName);
            if (!result.ok) throw new Error(result.error);
            log(`✅ Demo caption track created: ${result.label}`);
            setStatus("Demo captions inserted ✓", "ok");
        } catch (e) {
            log(e.message, "error");
            setStatus("Error — see log", "error");
        }
    });

    /* ── Real-Time Caption Editor ───────────────────────────────────────────── */
    function formatTime(sec) {
        const s = Math.max(0, parseFloat(sec) || 0);
        const mins = Math.floor(s / 60);
        const secs = Math.floor(s % 60);
        const tenths = Math.floor((s % 1) * 10);
        return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${tenths}`;
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function renderLineWordChips(line) {
        const currentKeyword = (line.keyword || "").trim().toLowerCase().replace(/[.,!?:;"'()[\]{}]/g, "");
        let words = line.words;
        if (!words || words.length === 0) {
            words = (line.text || "").split(/\s+/).filter(Boolean).map(w => ({ word: w, highlight: false }));
        }

        return words.map(w => {
            const rawWord = w.word || "";
            const cleanWord = rawWord.trim().toLowerCase().replace(/[.,!?:;"'()[\]{}]/g, "");
            const isHighlighted = w.highlight || (cleanWord && cleanWord === currentKeyword);
            return `<button class="word-chip${isHighlighted ? ' highlighted' : ''}" type="button" data-word="${escapeHtml(rawWord)}" title="Click to highlight '${escapeHtml(rawWord)}' in gold">${escapeHtml(rawWord)}</button>`;
        }).join("");
    }

    function renderCaptionEditorList() {
        if (!editorList) return;
        if (!activeCaptionLines || activeCaptionLines.length === 0) {
            editorList.innerHTML = `<div style="font-size:11px;color:var(--text-muted);text-align:center;padding:16px 8px;">
                Generate captions above to customize text & highlighted words in real-time.
            </div>`;
            if (editorLineCount) editorLineCount.textContent = "0 lines";
            return;
        }

        if (editorLineCount) {
            editorLineCount.textContent = `${activeCaptionLines.length} lines`;
        }

        editorList.innerHTML = activeCaptionLines.map(line => `
            <div class="caption-line-card" id="card-${line.id}" data-line-id="${line.id}">
                <div class="caption-line-top">
                    <button class="caption-line-timing" type="button" data-action="seek" data-start="${line.start}" title="Jump playhead to ${formatTime(line.start)}">
                        <span>⏱️</span> ${formatTime(line.start)} - ${formatTime(line.end)}
                    </button>
                    <span class="caption-line-status" id="status-${line.id}"></span>
                </div>
                <input type="text" class="caption-edit-input" id="input-${line.id}" data-line-id="${line.id}" value="${escapeHtml(line.text || '')}" placeholder="Edit line text…" aria-label="Line text" />
                <div class="caption-word-chips" id="chips-${line.id}" data-line-id="${line.id}">
                    ${renderLineWordChips(line)}
                </div>
            </div>
        `).join("");
    }

    async function loadCaptionLines(manifestPath = "") {
        try {
            const endpoint = manifestPath ? `/captions/lines?manifest_path=${encodeURIComponent(manifestPath)}` : "/captions/lines";
            const res = await apiGet(endpoint);
            if (res.ok && res.lines && res.lines.length > 0) {
                activeManifestPath = res.manifest_path || manifestPath;
                activeCaptionLines = res.lines;
                renderCaptionEditorList();
                log(`Loaded ${res.lines.length} caption lines for real-time editing.`);
                return true;
            } else {
                activeCaptionLines = [];
                renderCaptionEditorList();
                return false;
            }
        } catch (e) {
            console.warn("Could not load caption lines:", e.message);
            activeCaptionLines = [];
            renderCaptionEditorList();
            return false;
        }
    }

    async function handleWordHighlight(lineId, word) {
        const line = activeCaptionLines.find(l => l.id === lineId);
        if (!line) return;

        const cleanTarget = word.trim().toLowerCase().replace(/[.,!?:;"'()[\]{}]/g, "");
        const currentKw = (line.keyword || "").trim().toLowerCase().replace(/[.,!?:;"'()[\]{}]/g, "");
        if (cleanTarget === currentKw) {
            log(`Word '${word}' is already the highlighted focus keyword for ${lineId}.`);
            return;
        }

        const card = $(`card-${lineId}`);
        const statusEl = $(`status-${lineId}`);
        const chipsCont = $(`chips-${lineId}`);

        // 1. Optimistic UI: highlight clicked chip instantly
        if (chipsCont) {
            const allChips = chipsCont.querySelectorAll(".word-chip");
            allChips.forEach(c => {
                const cClean = (c.dataset.word || "").trim().toLowerCase().replace(/[.,!?:;"'()[\]{}]/g, "");
                if (cClean === cleanTarget) {
                    c.classList.add("highlighted");
                } else {
                    c.classList.remove("highlighted");
                }
            });
        }
        if (card) {
            document.querySelectorAll(".caption-line-card").forEach(c => c.classList.remove("active-line"));
            card.classList.add("active-line");
        }
        if (statusEl) {
            statusEl.textContent = "⚡ Rendering…";
            statusEl.className = "caption-line-status updating";
        }

        try {
            // 2. Request backend to re-render ONLY this clip (~0.7s on GPU)
            const payload = {
                manifest_path: activeManifestPath,
                line_id: lineId,
                keyword: word
            };
            const res = await apiPost("/captions/edit", payload);
            if (!res.ok) throw new Error(res.error || "Failed to update line");

            // 3. Update local model
            if (res.line) {
                Object.assign(line, res.line);
            } else {
                line.keyword = word;
            }

            // 4. Replace clip on Video Track 2 in Premiere Pro in real-time!
            const replaceRes = await evalScript("SC_replaceCaptionClip", res.line_id, res.clip_path, res.start);
            if (!replaceRes.ok) {
                log(`Clip replacement notice: ${replaceRes.error}`, "warn");
            }

            // 5. Jump playhead to caption timestamp so editor sees live result in Program monitor!
            await evalScript("SC_setPlayhead", res.start);

            if (statusEl) {
                statusEl.textContent = "✓ Live on V2";
                statusEl.className = "caption-line-status updated";
                setTimeout(() => {
                    if (statusEl && statusEl.textContent === "✓ Live on V2") {
                        statusEl.textContent = "";
                        statusEl.className = "caption-line-status";
                    }
                }, 2200);
            }

            log(`⚡ Word '${word}' highlighted in gold & updated on V2 at ${formatTime(res.start)}!`);
        } catch (err) {
            log(`Failed to customize highlight for ${lineId}: ${err.message}`, "error");
            if (statusEl) {
                statusEl.textContent = "⚠️ Error";
                statusEl.className = "caption-line-status error";
            }
            if (chipsCont) {
                chipsCont.innerHTML = renderLineWordChips(line);
            }
        }
    }

    async function handleTextChange(lineId, newText) {
        const line = activeCaptionLines.find(l => l.id === lineId);
        if (!line) return;

        if (newText.trim() === (line.text || "").trim()) return;

        const card = $(`card-${lineId}`);
        const statusEl = $(`status-${lineId}`);
        const inputEl = $(`input-${lineId}`);
        const chipsCont = $(`chips-${lineId}`);

        if (card) {
            document.querySelectorAll(".caption-line-card").forEach(c => c.classList.remove("active-line"));
            card.classList.add("active-line");
        }
        if (statusEl) {
            statusEl.textContent = "⚡ Updating text…";
            statusEl.className = "caption-line-status updating";
        }

        try {
            const payload = {
                manifest_path: activeManifestPath,
                line_id: lineId,
                text: newText,
                keyword: line.keyword
            };
            const res = await apiPost("/captions/edit", payload);
            if (!res.ok) throw new Error(res.error || "Failed to update line text");

            if (res.line) {
                Object.assign(line, res.line);
            } else {
                line.text = newText;
            }

            if (inputEl) inputEl.value = line.text;
            if (chipsCont) chipsCont.innerHTML = renderLineWordChips(line);

            // Replace clip on Video Track 2 in Premiere Pro
            const replaceRes = await evalScript("SC_replaceCaptionClip", res.line_id, res.clip_path, res.start);
            if (!replaceRes.ok) {
                log(`Clip replacement notice: ${replaceRes.error}`, "warn");
            }

            // Jump playhead so editor sees update immediately
            await evalScript("SC_setPlayhead", res.start);

            if (statusEl) {
                statusEl.textContent = "✓ Live on V2";
                statusEl.className = "caption-line-status updated";
                setTimeout(() => {
                    if (statusEl && statusEl.textContent === "✓ Live on V2") {
                        statusEl.textContent = "";
                        statusEl.className = "caption-line-status";
                    }
                }, 2200);
            }

            log(`✏️ Line ${lineId} text updated to "${newText}" & replaced on V2 at ${formatTime(res.start)}!`);
        } catch (err) {
            log(`Failed to update text for ${lineId}: ${err.message}`, "error");
            if (statusEl) {
                statusEl.textContent = "⚠️ Error";
                statusEl.className = "caption-line-status error";
            }
            if (inputEl) inputEl.value = line.text;
        }
    }

    /* ── Event Listeners ───────────────────────────────────────────────────── */
    btnInfo.addEventListener("click", detectSequenceMedia);
    btnGenerate.addEventListener("click", generateAndInsertCaptions);
    btnPing.addEventListener("click", () => pingEngine(false));

    if (btnRefreshCaptions) {
        btnRefreshCaptions.addEventListener("click", () => {
            log("Refreshing caption lines from engine…");
            loadCaptionLines(activeManifestPath);
        });
    }

    if (editorList) {
        editorList.addEventListener("click", e => {
            // 1. Timing button clicked -> seek playhead
            const timingBtn = e.target.closest("[data-action='seek']");
            if (timingBtn) {
                const startSec = parseFloat(timingBtn.dataset.start);
                evalScript("SC_setPlayhead", startSec);
                const card = timingBtn.closest(".caption-line-card");
                if (card) {
                    document.querySelectorAll(".caption-line-card").forEach(c => c.classList.remove("active-line"));
                    card.classList.add("active-line");
                }
                return;
            }

            // 2. Word chip clicked -> highlight in real time
            const chip = e.target.closest(".word-chip");
            if (chip) {
                const word = chip.dataset.word;
                const card = chip.closest(".caption-line-card");
                const lineId = card ? card.dataset.lineId : null;
                if (lineId && word) {
                    handleWordHighlight(lineId, word);
                }
                return;
            }
        });

        editorList.addEventListener("keydown", e => {
            if (e.target.classList.contains("caption-edit-input") && e.key === "Enter") {
                e.preventDefault();
                e.target.blur();
            }
        });

        editorList.addEventListener("change", e => {
            if (e.target.classList.contains("caption-edit-input")) {
                const lineId = e.target.dataset.lineId;
                const newText = e.target.value.trim();
                if (lineId) {
                    handleTextChange(lineId, newText);
                }
            }
        });
    }

    const btnReload = $("btn-reload");
    if (btnReload) {
        btnReload.addEventListener("click", () => {
            try {
                if (typeof require === "function") {
                    const fs = require("fs");
                    const path = require("path");
                    const extPath = cs.getSystemPath(SystemPath.EXTENSION);
                    const hostPath = path.join(extPath, "jsx", "host.jsx");
                    if (fs.existsSync(hostPath)) {
                        const jsx = fs.readFileSync(hostPath, "utf8");
                        cs.evalScript(jsx);
                    }
                }
            } catch (e) {}
            window.location.reload(true);
        });
    }

    /* ── Boot ──────────────────────────────────────────────────────────────── */
    async function boot() {
        log(`StyleCut panel v${PANEL_VERSION} initialized (${appName} ${appVersion})`);

        // Force reload host.jsx directly into ExtendScript memory on each panel load
        try {
            cs.evalScript("function SC_upgradeCaptionsToGraphics(seq) { return { ok: false }; }");
            if (typeof require === "function") {
                const fs = require("fs");
                const path = require("path");
                const extPath = cs.getSystemPath(SystemPath.EXTENSION);
                const hostPath = path.join(extPath, "jsx", "host.jsx");
                if (fs.existsSync(hostPath)) {
                    const jsx = fs.readFileSync(hostPath, "utf8");
                    cs.evalScript(jsx, res => {
                        log("ExtendScript host engine synchronized.");
                    });
                }
            }
        } catch (e) {
            console.warn("Could not reload host.jsx via Node fs:", e);
        }

        setTimeout(() => pingEngine(true), 400);

        // Safe Theme Color Matching
        try {
            const hostEnv = typeof cs.getHostEnvironment === "function" ? cs.getHostEnvironment() : null;
            const bg = hostEnv && hostEnv.appSkinInfo ? hostEnv.appSkinInfo.panelBackgroundColor : null;
            if (bg && bg.color) {
                const c = bg.color;
                document.body.style.setProperty("--pp-bg", `rgb(${c.red},${c.green},${c.blue})`);
            }
        } catch (e) {
            console.warn("Could not read host skin info:", e);
        }

        // Try detecting initial sequence after brief delay
        setTimeout(detectSequenceMedia, 800);

        // Automatically populate caption editor if captions already exist
        setTimeout(() => loadCaptionLines(), 1200);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

    // Expose for testing and debugging from DevTools
    window._SC = { evalScript, pingEngine, detectSequenceMedia, generateAndInsertCaptions, loadCaptionLines, handleWordHighlight, handleTextChange, boot };

})();
