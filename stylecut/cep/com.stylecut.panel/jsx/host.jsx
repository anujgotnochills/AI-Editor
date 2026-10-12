/**
 * host.jsx  —  StyleCut ExtendScript host
 * =========================================
 * Runs inside Premiere Pro's ExtendScript engine (ES3 strict-ish).
 * Called from the panel via CSInterface.evalScript("functionName(args)").
 *
 * API surface (all return JSON strings so the panel can JSON.parse them):
 *
 *   SC_getActiveSequenceInfo()   → { name, duration, frameRate, mediaPath? }
 *   SC_importSRT(srtPath, lang)  → { ok, trackIndex } | { ok, error }
 *   SC_getVersion()              → { premiere, os }
 *
 * KNOWN PREMIERE SCRIPTING LIMITATIONS (documented, not faked):
 *   1. app.project.activeSequence.audioTracks — readable, but CEP/ExtendScript
 *      has NO native "Caption Track" object. Captions must be imported via
 *      app.project.importFiles([srtPath]) then placed on the timeline as a
 *      caption clip using app.project.activeSequence.videoTracks[].clips.add().
 *      The "Captions" track type (SubtitlesTrack) is NOT accessible from
 *      ExtendScript in Premiere < 22.6. In 22.6+ you can use
 *      sequence.createCaptionTrack() — this script detects the version and
 *      falls back to the import-and-place method.
 *   2. Media path retrieval: app.project.activeSequence has no direct
 *      .mediaPath. We walk project items to find master clips.
 *   3. Audio export from the panel: NOT available via ExtendScript alone.
 *      The engine must call ffmpeg on the file path we return.
 */

// ─── Namespace guard ──────────────────────────────────────────────────────────
var SC = SC || {};

// ─── Utility ─────────────────────────────────────────────────────────────────

SC._ok = function (payload) {
    payload.ok = true;
    return JSON.stringify(payload);
};

SC._err = function (msg) {
    return JSON.stringify({ ok: false, error: String(msg) });
};

SC._ppVersion = function () {
    // Returns numeric major.minor, e.g. 22.6
    try {
        var v = app.version; // "22.6.0 (Build 2)"
        var parts = v.split(".");
        return parseFloat(parts[0] + "." + parts[1]);
    } catch (e) {
        return 0;
    }
};

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * SC_getActiveSequenceInfo
 * Returns metadata for the currently active sequence, plus the file path
 * of the first master clip found in the project (heuristic; good enough
 * for single-clip projects typical to StyleCut).
 */
function SC_getActiveSequenceInfo() {
    try {
        var seq = app.project.activeSequence;
        if (!seq) {
            return SC._err("No active sequence. Open a sequence in the timeline first.");
        }

        var fps = 0;
        try { fps = parseFloat(seq.timebase); } catch (e) {}

        function cleanClipName(n) {
            if (!n) return "";
            var s = String(n).replace(/\s*\[[VA][0-9]*\]/gi, "");
            s = s.replace(/\.[a-zA-Z0-9]{2,4}$/i, "");
            return s.toLowerCase();
        }

        // Helper: search project panel for clip media path by name
        function findMediaByClipName(targetName, folder) {
            if (!targetName) return "";
            var cleanTarget = cleanClipName(targetName);
            if (!folder) folder = app.project.rootItem;
            for (var i = 0; i < folder.children.numItems; i++) {
                var item = folder.children[i];
                if (item.type === ProjectItemType.CLIP) {
                    var cleanItem = cleanClipName(item.name);
                    if (cleanItem === cleanTarget || (cleanTarget.length > 2 && cleanItem.indexOf(cleanTarget) !== -1) || (cleanItem.length > 2 && cleanTarget.indexOf(cleanItem) !== -1)) {
                        var mp = "";
                        try { mp = item.getMediaPath(); } catch (e) {}
                        if (mp) return mp;
                    }
                } else if (item.type === ProjectItemType.BIN) {
                    var sub = findMediaByClipName(targetName, item);
                    if (sub) return sub;
                }
            }
            return "";
        }

        // Helper to extract clean timing info from any TrackItem
        function getClipTiming(clip, trackIdx, trackType) {
            var mp = "";
            try {
                if (clip.projectItem && typeof clip.projectItem.getMediaPath === "function") {
                    mp = clip.projectItem.getMediaPath();
                }
            } catch (e1) {}
            if (!mp && clip.name) {
                mp = findMediaByClipName(clip.name);
            }

            var startSec = 0;
            try { if (clip.start && typeof clip.start.seconds !== "undefined") startSec = parseFloat(clip.start.seconds); } catch (e2) {}

            var endSec = 0;
            try { if (clip.end && typeof clip.end.seconds !== "undefined") endSec = parseFloat(clip.end.seconds); } catch (e3) {}

            var durSec = 0;
            try { if (clip.duration && typeof clip.duration.seconds !== "undefined") durSec = parseFloat(clip.duration.seconds); } catch (e4) {}
            if (durSec <= 0 && endSec > startSec) durSec = endSec - startSec;
            if (endSec <= startSec && durSec > 0) endSec = startSec + durSec;

            var inSec = 0;
            try { if (clip.inPoint && typeof clip.inPoint.seconds !== "undefined") inSec = parseFloat(clip.inPoint.seconds); } catch (e5) {}

            var outSec = 0;
            try { if (clip.outPoint && typeof clip.outPoint.seconds !== "undefined") outSec = parseFloat(clip.outPoint.seconds); } catch (e6) {}
            if (outSec <= inSec && durSec > 0) {
                outSec = inSec + durSec;
            }

            return {
                name: clip.name || "",
                media_path: mp,
                track_type: trackType,
                track: trackIdx,
                start: startSec,
                end: endSec,
                duration: durSec,
                in_point: inSec,
                out_point: outSec
            };
        }

        // 1. Inspect Video Tracks (V1 is primary source for video media)
        var videoClips = [];
        if (seq.videoTracks && seq.videoTracks.numTracks > 0) {
            for (var vt = 0; vt < seq.videoTracks.numTracks; vt++) {
                var vTrack = seq.videoTracks[vt];
                if (vTrack.clips && vTrack.clips.numItems > 0) {
                    for (var vc = 0; vc < vTrack.clips.numItems; vc++) {
                        var vClip = vTrack.clips[vc];
                        videoClips.push(getClipTiming(vClip, vt, "video"));
                    }
                }
            }
        }

        // 2. Inspect Audio Tracks
        var audioClips = [];
        if (seq.audioTracks && seq.audioTracks.numTracks > 0) {
            for (var at = 0; at < seq.audioTracks.numTracks; at++) {
                var aTrack = seq.audioTracks[at];
                if (aTrack.clips && aTrack.clips.numItems > 0) {
                    for (var ac = 0; ac < aTrack.clips.numItems; ac++) {
                        var aClip = aTrack.clips[ac];
                        audioClips.push(getClipTiming(aClip, at, "audio"));
                    }
                }
            }
        }

        // Prefer video tracks, fallback to audio tracks
        var timelineClips = (videoClips.length > 0) ? videoClips : audioClips;

        var mediaPath = "";
        for (var tc = 0; tc < timelineClips.length; tc++) {
            if (timelineClips[tc].media_path) {
                mediaPath = timelineClips[tc].media_path;
                break;
            }
        }
        if (!mediaPath && audioClips.length > 0) {
            for (var ac = 0; ac < audioClips.length; ac++) {
                if (audioClips[ac].media_path) {
                    mediaPath = audioClips[ac].media_path;
                    break;
                }
            }
        }

        // Sort timeline clips chronologically
        timelineClips.sort(function(a, b) { return a.start - b.start; });

        var TICKS_PER_SEC = 254016000000;
        var seqDur = 0;
        try {
            if (seq.end) {
                if (typeof seq.end.seconds !== "undefined") {
                    seqDur = parseFloat(seq.end.seconds);
                } else {
                    seqDur = parseFloat(seq.end) / TICKS_PER_SEC;
                }
            }
        } catch (eSeq) {}

        if (seqDur <= 0 && timelineClips.length > 0) {
            for (var tc = 0; tc < timelineClips.length; tc++) {
                if (timelineClips[tc].end > seqDur) {
                    seqDur = timelineClips[tc].end;
                }
            }
        }
        var fw = 1080;
        var fh = 1920;
        try {
            if (seq.frameSizeHorizontal) fw = parseInt(seq.frameSizeHorizontal, 10);
            if (seq.frameSizeVertical) fh = parseInt(seq.frameSizeVertical, 10);
        } catch (eDim) {}

        return SC._ok({
            name: seq.name,
            id: seq.sequenceID,
            duration: seqDur,
            sequenceDuration: seqDur,
            frameRate: fps,
            frameWidth: fw,
            frameHeight: fh,
            mediaPath: mediaPath,
            timelineClips: timelineClips,
            clipCount: timelineClips.length,
            ppVersion: app.version
        });
    } catch (e) {
        return SC._err("SC_getActiveSequenceInfo failed: " + e.message);
    }
}

/**
 * SC_exportTimelineAudio
 * Directly exports the active sequence timeline audio mix to a WAV file using
 * Premiere's native exportAsMediaDirect. This guarantees 100% frame-accurate
 * audio matching all cuts, trims, arrangements, and multi-track audio on the timeline.
 *
 * @param {string} customOutPath     Optional target output WAV path.
 * @param {string} customPresetPath  Optional .epr preset path.
 * @returns JSON string: { ok, path, size, duration, sequenceName }
 */
function SC_exportTimelineAudio(customOutPath, customPresetPath) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) {
            return SC._err("No active sequence. Open a sequence in the timeline first.");
        }

        var TICKS_PER_SEC = 254016000000;
        var seqDur = 0;
        try {
            if (seq.end) {
                if (typeof seq.end.seconds !== "undefined") {
                    seqDur = parseFloat(seq.end.seconds);
                } else {
                    seqDur = parseFloat(seq.end) / TICKS_PER_SEC;
                }
            }
        } catch (eDur) {}

        // Determine output path in OS Temp folder
        var outPath = customOutPath;
        if (!outPath || typeof outPath !== "string" || outPath.length === 0) {
            outPath = Folder.temp.fsName + "/stylecut_timeline_" + (new Date().getTime()) + ".wav";
        }
        outPath = String(outPath).replace(/^file:\/*/i, "");
        try { outPath = decodeURIComponent(outPath); } catch (eClean) {}

        // Resolve preset file
        var presetFile = null;
        if (customPresetPath) {
            var cp = String(customPresetPath).replace(/^file:\/*/i, "");
            try { cp = decodeURIComponent(cp); } catch (e2) {}
            var pCustom = new File(cp);
            if (pCustom.exists) presetFile = pCustom;
        }

        if (!presetFile) {
            // 1. Relative to host.jsx / extension
            try {
                var thisScript = new File($.fileName);
                if (thisScript.exists && thisScript.parent && thisScript.parent.parent) {
                    var cand1 = new File(thisScript.parent.parent.fsName + "/presets/stylecut-audio.epr");
                    if (cand1.exists) presetFile = cand1;
                }
            } catch (ePreset1) {}
        }

        if (!presetFile) {
            // 2. CEP extension folder in AppData
            var cand2 = new File(Folder.userData.fsName + "/Adobe/CEP/extensions/com.stylecut.panel/presets/stylecut-audio.epr");
            if (cand2.exists) presetFile = cand2;
        }

        if (!presetFile) {
            // 3. captik-audio.epr fallback
            var cand3 = new File(Folder.userData.fsName + "/Adobe/CEP/extensions/com.captik.panel/presets/captik-audio.epr");
            if (cand3.exists) presetFile = cand3;
        }

        if (!presetFile || !presetFile.exists) {
            return SC._err("Audio export preset stylecut-audio.epr not found on disk.");
        }

        var outFile = new File(outPath);
        if (outFile.parent && !outFile.parent.exists) {
            outFile.parent.create();
        }
        if (outFile.exists) {
            try { outFile.remove(); } catch (eRem) {}
        }

        // exportAsMediaDirect(outputPath, presetPath, workAreaType)
        // 0 = ENCODE_ENTIRE (entire sequence timeline mix)
        // Synchronous call in Premiere Pro
        var exportResult = seq.exportAsMediaDirect(outFile.fsName, presetFile.fsName, 0);

        if (!outFile.exists || outFile.length < 500) {
            return SC._err("seq.exportAsMediaDirect finished but output file missing or empty (return=" + exportResult + ")");
        }

        return SC._ok({
            path: outFile.fsName,
            size: outFile.length,
            duration: seqDur,
            sequenceName: seq.name
        });
    } catch (e) {
        return SC._err("SC_exportTimelineAudio failed: " + e.message);
    }
}


/**
 * SC_importSRT
 * Imports an SRT file from disk and creates a caption track on the active
 * sequence. Supports Premiere Pro 22.6+ native caption track API; falls back
 * to the clip-import method for older versions.
 *
 * @param {string} srtPath   Absolute Windows path to the .srt file.
 * @param {string} lang      "en" or "hinglish" (used only for track naming).
 * @returns JSON string: { ok, trackIndex } or { ok, error }
 *
 * LIMITATION: In Premiere < 22.6, SRT files are imported as subtitle clips
 * placed on video track 2. They ARE editable (double-click → caption editor)
 * but live on a video track, not a dedicated Captions track. In 22.6+, a
 * true caption track is created via sequence.createCaptionTrack().
 */
function SC_upgradeCaptionsToGraphics(seq) {
    // Premiere Pro ExtendScript does not have an official app.findMenuCommandId API.
    // Captions are now placed automatically as transparent graphic overlays via SC_importGraphicOverlay.
    return { ok: false, cmdID: 0, name: "", diag: ["native_menu_unsupported"] };
}

function SC_importSRT(srtPath, lang, formatMode, templateName) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) {
            return SC._err("No active sequence.");
        }
        if (!srtPath || srtPath.length === 0) {
            return SC._err("srtPath is empty.");
        }

        formatMode = formatMode || "graphics";
        templateName = templateName || "hormozi";

        var label = (lang === "hinglish") ? "StyleCut Hinglish" : "StyleCut English";

        // 1. Ensure file exists on disk
        var srtFile = new File(srtPath);
        if (!srtFile.exists) {
            return SC._err("SRT file does not exist on disk: " + srtPath);
        }

        // 2. Import SRT into project root
        var suppressWarnings = true;
        var importPath = srtFile.fsName;
        app.project.importFiles([importPath], suppressWarnings, app.project.rootItem, false);

        // 3. Locate the imported ProjectItem
        var srtItem = null;
        var fileName = srtFile.name;
        var normTarget = importPath.replace(/\\/g, "/").toLowerCase();

        function findItem(folder) {
            if (!folder || !folder.children) return null;
            for (var i = 0; i < folder.children.numItems; i++) {
                var child = folder.children[i];
                if (!child) continue;
                var mp = "";
                try { mp = child.getMediaPath(); } catch (pe) {}
                if (mp) {
                    var normMp = mp.replace(/\\/g, "/").toLowerCase();
                    if (normMp === normTarget || normMp.indexOf(fileName.toLowerCase()) !== -1) {
                        return child;
                    }
                }
                if (child.name === fileName || child.name.indexOf(fileName) !== -1) {
                    return child;
                }
                if (child.type === ProjectItemType.BIN) {
                    var sub = findItem(child);
                    if (sub) return sub;
                }
            }
            return null;
        }

        srtItem = findItem(app.project.rootItem);

        // Fallback: check last child of rootItem
        if (!srtItem && app.project.rootItem.children.numItems > 0) {
            var last = app.project.rootItem.children[app.project.rootItem.children.numItems - 1];
            if (last) {
                srtItem = last;
            }
        }

        if (!srtItem) {
            return SC._err("SRT file was imported, but could not locate it in the Project panel.");
        }

        // 4. Place onto timeline
        var created = false;
        var methodUsed = "";

        // ── Primary: Premiere Pro Native Caption Track ────────────────────────
        if (typeof seq.createCaptionTrack === "function") {
            try {
                // seq.createCaptionTrack(projectItem, startTimeSeconds, [captionFormat])
                var trackResult = seq.createCaptionTrack(srtItem, 0);
                if (trackResult !== false) {
                    created = true;
                    methodUsed = "createCaptionTrack";
                }
            } catch (errCT1) {
                $.writeln("[StyleCut] createCaptionTrack(srtItem, 0) threw: " + errCT1.message);
                try {
                    var t0 = new Time();
                    t0.seconds = 0;
                    var trackResult2 = seq.createCaptionTrack(srtItem, t0);
                    if (trackResult2 !== false) {
                        created = true;
                        methodUsed = "createCaptionTrack(Time)";
                    }
                } catch (errCT2) {
                    $.writeln("[StyleCut] createCaptionTrack(srtItem, Time) threw: " + errCT2.message);
                }
            }
        }

        // ── Secondary: Overwrite clip onto video track ────────────────────────
        if (!created) {
            var vTracks = seq.videoTracks;
            if (!vTracks || vTracks.numTracks === 0) {
                return SC._err("Sequence has no video tracks.");
            }

            // Prefer V2 if available, else V1
            var targetTrackIndex = (vTracks.numTracks > 1) ? 1 : 0;
            var targetTrack = vTracks[targetTrackIndex];

            try {
                if (typeof targetTrack.overwriteClip === "function") {
                    targetTrack.overwriteClip(srtItem, 0);
                    created = true;
                    methodUsed = "track.overwriteClip";
                } else if (typeof seq.overwriteClip === "function") {
                    seq.overwriteClip(srtItem, 0, targetTrackIndex, 0);
                    created = true;
                    methodUsed = "seq.overwriteClip";
                } else if (typeof targetTrack.insertClip === "function") {
                    targetTrack.insertClip(srtItem, 0);
                    created = true;
                    methodUsed = "track.insertClip";
                }
            } catch (clipErr) {
                $.writeln("[StyleCut] Video track overwrite failed: " + clipErr.message);
                try {
                    var t1 = new Time();
                    t1.seconds = 0;
                    if (typeof targetTrack.overwriteClip === "function") {
                        targetTrack.overwriteClip(srtItem, t1);
                        created = true;
                        methodUsed = "track.overwriteClip(Time)";
                    }
                } catch (clipErr2) {
                    return SC._err("Could not place SRT on timeline: " + clipErr.message);
                }
            }
        }

        if (!created) {
            return SC._err("SRT was imported into Project panel, but could not be placed onto the sequence.");
        }

        // ── 5. Convert to Essential Graphics (Video Track) if requested ───────
        var upgradedToGraphics = false;
        var upgradeInfo = null;

        if (formatMode === "graphics") {
            upgradeInfo = SC_upgradeCaptionsToGraphics(seq);
            if (upgradeInfo && upgradeInfo.ok) {
                upgradedToGraphics = true;
                methodUsed = "Essential Graphics (" + upgradeInfo.name + ")";
            }
        }

        return SC._ok({
            method: methodUsed,
            format: formatMode,
            template: templateName,
            upgraded: upgradedToGraphics,
            label: label,
            ppVersion: app.version,
            note: upgradedToGraphics 
                ? "Captions converted to Essential Graphics on Video Track. Video effects (drop shadow, transform, glow, blur) can now be applied!"
                : (formatMode === "graphics" 
                    ? "Captions placed on Subtitle track. To convert to graphics, select 'Sequence > Upgrade Caption to Graphic' in Premiere Pro."
                    : "Captions added to Subtitle track.")
        });

    } catch (e) {
        return SC._err("SC_importSRT failed: " + e.message);
    }
}

/**
 * SC_getVersion
 * Diagnostic: returns app and OS version strings.
 */
function SC_getVersion() {
    try {
        return SC._ok({
            premiere: app.version,
            os: $.os,
            buildNumber: app.buildNumber
        });
    } catch (e) {
        return SC._err("SC_getVersion failed: " + e.message);
    }
}

/**
 * SC_findProjectMediaPaths
 * Returns all video/audio file paths in the project (for the engine to pick up).
 * Useful when the user wants to transcribe a clip already in the project.
 */
function SC_findProjectMediaPaths() {
    try {
        var paths = [];
        var root = app.project.rootItem;
        function walk(folder) {
            for (var i = 0; i < folder.children.numItems; i++) {
                var item = folder.children[i];
                if (item.type === ProjectItemType.CLIP) {
                    var p = "";
                    try { p = item.getMediaPath(); } catch (e2) {}
                    if (p) paths.push({ name: item.name, path: p });
                } else if (item.type === ProjectItemType.BIN) {
                    walk(item);
                }
            }
        }
        walk(root);
        return SC._ok({ paths: paths });
    } catch (e) {
        return SC._err("SC_findProjectMediaPaths failed: " + e.message);
    }
}

/**
 * SC_importGraphicOverlay
 * Imports a transparent ProRes 4444 .mov video overlay and places it on Video Track 2 (V2).
 * This renders rich viral captions (colors, sizes, glow, drop shadow) directly onto
 * the video track without requiring any manual Track Style or upgrade step!
 */
function SC_importGraphicOverlay(movPath) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) {
            return SC._err("No active sequence.");
        }
        if (!movPath || movPath.length === 0) {
            return SC._err("movPath is empty.");
        }

        var movFile = new File(movPath);
        if (!movFile.exists) {
            return SC._err("Overlay video file does not exist on disk: " + movPath);
        }

        // 1. Import MOV into project root
        var suppressWarnings = true;
        var importPath = movFile.fsName;
        app.project.importFiles([importPath], suppressWarnings, app.project.rootItem, false);

        // 2. Locate the imported ProjectItem
        var movItem = null;
        var fileName = movFile.name;
        var normTarget = importPath.replace(/\\/g, "/").toLowerCase();

        function findItem(folder) {
            if (!folder || !folder.children) return null;
            for (var i = 0; i < folder.children.numItems; i++) {
                var child = folder.children[i];
                if (!child) continue;
                var mp = "";
                try { mp = child.getMediaPath(); } catch (pe) {}
                if (mp) {
                    var normMp = mp.replace(/\\/g, "/").toLowerCase();
                    if (normMp === normTarget || normMp.indexOf(fileName.toLowerCase()) !== -1) {
                        return child;
                    }
                }
                if (child.name === fileName || child.name.indexOf(fileName) !== -1) {
                    return child;
                }
                if (child.type === ProjectItemType.BIN) {
                    var sub = findItem(child);
                    if (sub) return sub;
                }
            }
            return null;
        }

        movItem = findItem(app.project.rootItem);
        if (!movItem && app.project.rootItem.children.numItems > 0) {
            movItem = app.project.rootItem.children[app.project.rootItem.children.numItems - 1];
        }

        if (!movItem) {
            return SC._err("Overlay video was imported, but could not locate it in Project panel.");
        }

        // 3. Place onto Video Track 2 (or lowest available track above V1)
        var vTracks = seq.videoTracks;
        if (!vTracks || vTracks.numTracks === 0) {
            return SC._err("Sequence has no video tracks.");
        }

        var targetTrackIndex = (vTracks.numTracks > 1) ? 1 : 0;
        var targetTrack = vTracks[targetTrackIndex];
        var placed = false;

        try {
            if (typeof targetTrack.overwriteClip === "function") {
                targetTrack.overwriteClip(movItem, 0);
                placed = true;
            } else if (typeof seq.overwriteClip === "function") {
                seq.overwriteClip(movItem, 0, targetTrackIndex, 0);
                placed = true;
            }
        } catch (clipErr) {
            try {
                var t0 = new Time();
                t0.seconds = 0;
                targetTrack.overwriteClip(movItem, t0);
                placed = true;
            } catch (clipErr2) {
                return SC._err("Could not place overlay onto video track: " + clipErr.message);
            }
        }

        if (!placed) {
            return SC._err("Could not overwrite overlay clip onto video track.");
        }

        return SC._ok({
            method: "Graphic Video Overlay",
            track: "V" + (targetTrackIndex + 1),
            label: "StyleCut Viral Captions",
            path: importPath,
            note: "Captions placed directly on Video Track V" + (targetTrackIndex + 1) + " as a transparent graphic overlay with exact typography and styling!"
        });

    } catch (e) {
        return SC._err("SC_importGraphicOverlay failed: " + e.message);
    }
}

/**
 * SC_importCaptionsManifest
 * Imports all individual Remotion per-line clips from captions_manifest.json,
 * organizes them inside a dedicated "StyleCut Captions" bin, and places each clip
 * at its exact timestamp on Video Track 2 (V2).
 */
function SC_importCaptionsManifest(manifestPath) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) {
            return SC._err("No active sequence.");
        }
        if (!manifestPath || manifestPath.length === 0) {
            return SC._err("manifestPath is empty.");
        }

        var mFile = new File(manifestPath);
        if (!mFile.exists) {
            return SC._err("Manifest file does not exist on disk: " + manifestPath);
        }

        mFile.open("r");
        mFile.encoding = "UTF-8";
        var content = mFile.read();
        mFile.close();

        var manifestData = null;
        try {
            manifestData = JSON.parse(content);
        } catch (je) {
            manifestData = eval("(" + content + ")");
        }

        if (!manifestData || !manifestData.clips || manifestData.clips.length === 0) {
            return SC._err("Manifest contains no clips to place.");
        }

        // 1. Find or create "StyleCut Captions" Bin
        var captionsBin = null;
        var root = app.project.rootItem;
        for (var b = 0; b < root.children.numItems; b++) {
            var item = root.children[b];
            if (item && item.type === ProjectItemType.BIN && item.name === "StyleCut Captions") {
                captionsBin = item;
                break;
            }
        }
        if (!captionsBin) {
            captionsBin = root.createBin("StyleCut Captions");
        }

        // 2. Target Video Track (V2 if available, else V1)
        var vTracks = seq.videoTracks;
        if (!vTracks || vTracks.numTracks === 0) {
            return SC._err("Sequence has no video tracks.");
        }
        var targetTrackIndex = (vTracks.numTracks > 1) ? 1 : 0;
        var targetTrack = vTracks[targetTrackIndex];

        // 3. Collect valid file paths
        var clips = manifestData.clips;
        var filePaths = [];
        for (var i = 0; i < clips.length; i++) {
            if (clips[i].path) {
                var f = new File(clips[i].path);
                if (f.exists) {
                    filePaths.push(f.fsName);
                }
            }
        }

        if (filePaths.length === 0) {
            return SC._err("None of the clip files in manifest exist on disk.");
        }

        // 4. Batch import clips into captionsBin
        app.project.importFiles(filePaths, true, captionsBin, false);

        // 5. Index imported ProjectItems by path & name
        var itemMap = {};
        for (var j = 0; j < captionsBin.children.numItems; j++) {
            var cItem = captionsBin.children[j];
            if (!cItem) continue;
            var mp = "";
            try { mp = cItem.getMediaPath(); } catch (eMp) {}
            if (mp) {
                itemMap[mp.replace(/\\/g, "/").toLowerCase()] = cItem;
            }
            if (cItem.name) {
                itemMap[cItem.name.toLowerCase()] = cItem;
            }
        }

        // 5b. Clear pre-existing caption clips on target track to avoid overlapping ghost overlays
        try {
            if (targetTrack.clips && targetTrack.clips.numItems > 0) {
                for (var ci = targetTrack.clips.numItems - 1; ci >= 0; ci--) {
                    var trkClip = targetTrack.clips[ci];
                    if (trkClip && typeof trkClip.remove === "function") {
                        trkClip.remove(false, false);
                    }
                }
            }
        } catch (eTrkClean) {}

        // 6. Place each clip onto target video track at its start time
        var placedCount = 0;
        for (var k = 0; k < clips.length; k++) {
            var clip = clips[k];
            var clipFile = new File(clip.path);
            var normKey = clipFile.fsName.replace(/\\/g, "/").toLowerCase();
            var pItem = itemMap[normKey] || itemMap[clipFile.name.toLowerCase()];

            if (!pItem) {
                for (var m = 0; m < captionsBin.children.numItems; m++) {
                    var cand = captionsBin.children[m];
                    if (cand && cand.name.indexOf(clip.lineId) !== -1) {
                        pItem = cand;
                        break;
                    }
                }
            }

            if (pItem) {
                var startTime = parseFloat(clip.start) || 0;
                var placed = false;
                try {
                    var tObj = new Time();
                    tObj.seconds = startTime;
                    targetTrack.overwriteClip(pItem, tObj);
                    placed = true;
                } catch (tErr) {
                    try {
                        targetTrack.overwriteClip(pItem, startTime);
                        placed = true;
                    } catch (tErr2) {}
                }
                if (placed) {
                    placedCount++;
                }
            }
        }

        return SC._ok({
            method: "Remotion Template Clips",
            track: "V" + (targetTrackIndex + 1),
            count: placedCount,
            total: clips.length,
            template: manifestData.templateId || "hook_gold",
            note: "Placed " + placedCount + " editable per-line caption clips on Video Track V" + (targetTrackIndex + 1)
        });

    } catch (e) {
        return SC._err("SC_importCaptionsManifest failed: " + e.message);
    }
}

/**
 * SC_replaceCaptionClip
 * Replaces a single caption clip on Video Track 2 when an editor edits a line.
 */
function SC_replaceCaptionClip(lineId, newClipPath, startTime) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) return SC._err("No active sequence.");
        if (!newClipPath) return SC._err("newClipPath is empty.");

        var cFile = new File(newClipPath);
        if (!cFile.exists) return SC._err("Clip file does not exist on disk: " + newClipPath);

        // 1. Find captionsBin
        var captionsBin = null;
        var root = app.project.rootItem;
        for (var b = 0; b < root.children.numItems; b++) {
            var item = root.children[b];
            if (item && item.type === ProjectItemType.BIN && item.name === "StyleCut Captions") {
                captionsBin = item;
                break;
            }
        }
        if (!captionsBin) captionsBin = root.createBin("StyleCut Captions");

        // 2. Import new clip
        app.project.importFiles([cFile.fsName], true, captionsBin, false);

        // 3. Find imported item
        var normTarget = cFile.fsName.replace(/\\/g, "/").toLowerCase();
        var pItem = null;
        for (var i = 0; i < captionsBin.children.numItems; i++) {
            var cand = captionsBin.children[i];
            if (!cand) continue;
            var mp = "";
            try { mp = cand.getMediaPath(); } catch (pe) {}
            if (mp && mp.replace(/\\/g, "/").toLowerCase() === normTarget) {
                pItem = cand;
                break;
            }
            if (cand.name === cFile.name) {
                pItem = cand;
                break;
            }
        }

        if (!pItem) return SC._err("Imported clip could not be located in Project panel.");

        // 4. Overwrite on target track
        var vTracks = seq.videoTracks;
        var targetTrackIndex = (vTracks.numTracks > 1) ? 1 : 0;
        var targetTrack = vTracks[targetTrackIndex];

        var tSec = parseFloat(startTime) || 0;
        try {
            var tObj = new Time();
            tObj.seconds = tSec;
            targetTrack.overwriteClip(pItem, tObj);
        } catch (oe1) {
            targetTrack.overwriteClip(pItem, tSec);
        }

        return SC._ok({
            method: "Replace Caption Clip",
            lineId: lineId,
            path: cFile.fsName,
            start: tSec,
            track: "V" + (targetTrackIndex + 1)
        });
    } catch (e) {
        return SC._err("SC_replaceCaptionClip failed: " + e.message);
    }
}

/**
 * SC_setPlayhead
 * Seeks Premiere Pro active sequence playhead to given seconds.
 */
function SC_setPlayhead(seconds) {
    try {
        var seq = app.project.activeSequence;
        if (!seq) return SC._err("No active sequence.");
        var t = new Time();
        t.seconds = parseFloat(seconds) || 0;
        seq.setPlayerPosition(t.ticks);
        return SC._ok({ seconds: seconds });
    } catch (e) {
        return SC._err("SC_setPlayhead failed: " + e.message);
    }
}

/**
 * SC_importFCPXML
 * Imports an FCPXML / Premiere XML sequence into the active Premiere Pro project.
 */
function SC_importFCPXML(xmlPath) {
    try {
        if (!xmlPath) return SC._err("xmlPath is empty.");
        var xFile = new File(xmlPath);
        if (!xFile.exists) return SC._err("XML file not found: " + xmlPath);

        var preSeqCount = app.project.sequences.numSequences;

        // Import the XML file
        var importSuccess = app.project.importFiles([xFile.fsName], true, app.project.rootItem, false);
        if (!importSuccess) {
            return SC._err("app.project.importFiles failed for XML: " + xFile.fsName);
        }

        // Check if a new sequence was added
        var newSeq = null;
        if (app.project.sequences.numSequences > preSeqCount) {
            newSeq = app.project.sequences[app.project.sequences.numSequences - 1];
            if (newSeq && typeof app.project.openSequence === "function") {
                try { app.project.openSequence(newSeq.sequenceID); } catch (eOpen) {}
            }
        }

        return SC._ok({
            method: "FCPXML Import",
            path: xFile.fsName,
            sequenceName: newSeq ? newSeq.name : xFile.name,
            totalSequences: app.project.sequences.numSequences
        });
    } catch (e) {
        return SC._err("SC_importFCPXML failed: " + e.message);
    }
}

/**
 * SC_createRoughCutSequence
 * ExtendScript fallback to build a rough cut sequence directly from cut_list.json.
 */
function SC_createRoughCutSequence(cutListJsonPath) {
    try {
        if (!cutListJsonPath) return SC._err("cutListJsonPath is empty.");
        var jFile = new File(cutListJsonPath);
        if (!jFile.exists) return SC._err("cut_list.json not found: " + cutListJsonPath);

        jFile.open("r");
        var content = jFile.read();
        jFile.close();

        var cutData = JSON.parse(content);
        var cuts = cutData.cuts || [];
        var srcMeta = cutData.source_clip || {};
        var srcPath = srcMeta.path;

        if (!srcPath) return SC._err("source_clip.path missing in cut list.");
        var srcFile = new File(srcPath);
        if (!srcFile.exists) return SC._err("Source media file does not exist: " + srcPath);

        // 1. Locate or import source ProjectItem
        var root = app.project.rootItem;
        var srcItem = null;
        var normSrc = srcFile.fsName.replace(/\\/g, "/").toLowerCase();

        function findItem(bin) {
            for (var i = 0; i < bin.children.numItems; i++) {
                var it = bin.children[i];
                if (it.type === ProjectItemType.CLIP) {
                    var mp = "";
                    try { mp = it.getMediaPath(); } catch (pe) {}
                    if (mp && mp.replace(/\\/g, "/").toLowerCase() === normSrc) {
                        return it;
                    }
                } else if (it.type === ProjectItemType.BIN) {
                    var found = findItem(it);
                    if (found) return found;
                }
            }
            return null;
        }

        srcItem = findItem(root);
        if (!srcItem) {
            app.project.importFiles([srcFile.fsName], true, root, false);
            srcItem = findItem(root);
        }
        if (!srcItem) return SC._err("Could not find or import source clip into project.");

        // 2. Create new rough cut sequence
        var seqName = (srcFile.name.replace(/\.[^\.]+$/, "")) + " — StyleCut Rough Cut";
        var newSeq = app.project.createNewSequence(seqName, "");
        if (!newSeq) return SC._err("Failed to create new sequence.");

        var vTrack = newSeq.videoTracks[0];
        var aTrack = newSeq.audioTracks[0];

        // 3. Assemble clips from cut list
        var placedCount = 0;
        for (var k = 0; k < cuts.length; k++) {
            var c = cuts[k];
            var inSec = parseFloat(c.source_in_sec) || 0;
            var outSec = parseFloat(c.source_out_sec) || (inSec + 1);
            var tlInSec = parseFloat(c.timeline_in_sec) || 0;

            try {
                // Set in/out on project item
                srcItem.setInPoint(inSec, 4);
                srcItem.setOutPoint(outSec, 4);

                var tObj = new Time();
                tObj.seconds = tlInSec;
                vTrack.overwriteClip(srcItem, tObj);
                placedCount++;
            } catch (cutErr) {
                $.writeln("[StyleCut] Cut placement warning at " + tlInSec + "s: " + cutErr.message);
            }

            // 4. Add marker if cut was flagged
            if (c.flagged || c.confidence < 0.70) {
                try {
                    var mObj = newSeq.markers.createMarker(tlInSec);
                    mObj.name = "Review Cut";
                    mObj.comments = "Cut #" + (k + 1) + " (" + (c.reason || "review") + ") Conf: " + (c.confidence || 0.6);
                } catch (mErr) {}
            }
        }

        // Reset in/out on source project item
        try {
            srcItem.clearInPoint();
            srcItem.clearOutPoint();
        } catch (cErr) {}

        return SC._ok({
            method: "ExtendScript Sequence Assembly",
            sequenceName: seqName,
            placedCuts: placedCount,
            totalCuts: cuts.length
        });
    } catch (e) {
        return SC._err("SC_createRoughCutSequence failed: " + e.message);
    }
}

/**
 * SC_spikeCutInPlace
 * Step (a) Spike: Performs an in-place cut on app.project.activeSequence,
 * removing two hard-coded ranges (5s-7s and 20s-22s),
 * rebuilding tracks V1 and A1 with separate contiguous clips and NO gaps.
 */
function SC_spikeCutInPlace() {
    try {
        var apiLogs = [];
        var ppVer = app.version || "unknown";
        apiLogs.push("Premiere Pro version: " + ppVer);

        var seq = app.project.activeSequence;
        if (!seq) {
            return SC._err("No active sequence. Open a sequence in the timeline first.");
        }
        apiLogs.push("Active sequence: '" + seq.name + "'");

        var vTracks = seq.videoTracks;
        if (!vTracks || vTracks.numTracks === 0) {
            return SC._err("Active sequence has no video tracks.");
        }
        var vTrack = vTracks[0];
        var aTrack = (seq.audioTracks && seq.audioTracks.numTracks > 0) ? seq.audioTracks[0] : null;

        var vClipsCount = vTrack.clips ? vTrack.clips.numItems : 0;
        if (vClipsCount === 0) {
            return SC._err("Video Track 1 has no clips. Place a clip on the sequence first.");
        }
        apiLogs.push("Initial V1 clips count: " + vClipsCount);

        function findProjectItemByName(targetName, folder) {
            if (!targetName) return null;
            var cleanTarget = cleanClipName(targetName);
            if (!folder) folder = app.project.rootItem;
            for (var f = 0; f < folder.children.numItems; f++) {
                var it = folder.children[f];
                if (it.type === ProjectItemType.CLIP) {
                    var cleanIt = cleanClipName(it.name);
                    if (cleanIt === cleanTarget || (cleanTarget.length > 2 && cleanIt.indexOf(cleanTarget) !== -1) || (cleanIt.length > 2 && cleanTarget.indexOf(cleanIt) !== -1)) {
                        return it;
                    }
                } else if (it.type === ProjectItemType.BIN) {
                    var sub = findProjectItemByName(targetName, it);
                    if (sub) return sub;
                }
            }
            return null;
        }

        // 1. Read existing clips on V1
        var origClips = [];
        for (var i = 0; i < vClipsCount; i++) {
            var c = vTrack.clips[i];
            var pItem = c.projectItem;
            if (!pItem && c.name) {
                pItem = findProjectItemByName(c.name);
            }
            var cStart = parseFloat(c.start.seconds) || 0;
            var cEnd = parseFloat(c.end.seconds) || 0;
            var cIn = parseFloat(c.inPoint.seconds) || 0;
            var cOut = parseFloat(c.outPoint.seconds) || (cIn + (cEnd - cStart));

            origClips.push({
                index: i,
                name: c.name || (pItem ? pItem.name : "Clip"),
                start: cStart,
                end: cEnd,
                duration: cEnd - cStart,
                inPoint: cIn,
                outPoint: cOut,
                projectItem: pItem
            });
            apiLogs.push("Read V1 clip " + i + ": '" + c.name + "' start=" + cStart.toFixed(2) + "s, end=" + cEnd.toFixed(2) + "s, in=" + cIn.toFixed(2) + "s, out=" + cOut.toFixed(2) + "s");
        }

        var origSeqDur = origClips[origClips.length - 1].end;
        apiLogs.push("Total sequence duration before cut: " + origSeqDur.toFixed(2) + "s");

        // 2. Define hard-coded cut ranges: remove [5s-7s] and [20s-22s]
        var cutRanges = [];
        var keepRanges = [];

        if (origSeqDur >= 23.0) {
            cutRanges = [
                { start: 5.0, end: 7.0 },
                { start: 20.0, end: 22.0 }
            ];
            keepRanges = [
                { start: 0.0, end: 5.0 },
                { start: 7.0, end: 20.0 },
                { start: 22.0, end: origSeqDur }
            ];
        } else if (origSeqDur >= 10.0) {
            var c1Start = Math.min(3.0, origSeqDur * 0.25);
            var c1End = c1Start + 2.0;
            var c2Start = Math.min(c1End + 2.0, origSeqDur * 0.65);
            var c2End = Math.min(c2Start + 2.0, origSeqDur * 0.85);
            cutRanges = [
                { start: c1Start, end: c1End },
                { start: c2Start, end: c2End }
            ];
            keepRanges = [
                { start: 0.0, end: c1Start },
                { start: c1End, end: c2Start },
                { start: c2End, end: origSeqDur }
            ];
        } else {
            return SC._err("Sequence duration (" + origSeqDur.toFixed(2) + "s) is too short to cut two ranges (need >= 10s).");
        }

        apiLogs.push("Cut ranges to remove: " + JSON.stringify(cutRanges));
        apiLogs.push("Keep ranges to preserve: " + JSON.stringify(keepRanges));

        // 3. Map each keep-range to source clip and source in/out:
        // srcIn = clip.inPoint + (rangeStart - clip.start)
        var segmentsToPlace = [];
        for (var kr = 0; kr < keepRanges.length; kr++) {
            var kRange = keepRanges[kr];
            var rStart = kRange.start;
            var rEnd = kRange.end;
            var rDur = rEnd - rStart;
            if (rDur <= 0.05) continue;

            var matchedClip = null;
            for (var mc = 0; mc < origClips.length; mc++) {
                var cand = origClips[mc];
                if (rStart >= cand.start - 0.005 && rStart < cand.end + 0.005) {
                    matchedClip = cand;
                    break;
                }
            }
            if (!matchedClip) matchedClip = origClips[0];

            var offsetInClip = rStart - matchedClip.start;
            var srcIn = matchedClip.inPoint + offsetInClip;
            var srcOut = srcIn + rDur;

            segmentsToPlace.push({
                segmentIndex: kr,
                projectItem: matchedClip.projectItem,
                rangeStart: rStart,
                rangeEnd: rEnd,
                duration: rDur,
                srcIn: srcIn,
                srcOut: srcOut
            });
            apiLogs.push("Mapped segment " + kr + ": [" + rStart.toFixed(2) + "s - " + rEnd.toFixed(2) + "s] (dur=" + rDur.toFixed(2) + "s) -> srcIn=" + srcIn.toFixed(2) + "s, srcOut=" + srcOut.toFixed(2) + "s");
        }

        // 4. Clear tracks V1 and A1 (iterating last to first)
        apiLogs.push("Clearing tracks V1 & A1 (iterating last to first)...");
        var vDeleted = 0;
        for (var vi = vTrack.clips.numItems - 1; vi >= 0; vi--) {
            try {
                var cItem = vTrack.clips[vi];
                if (typeof cItem.remove === "function") {
                    cItem.remove(false, false);
                    vDeleted++;
                } else {
                    apiLogs.push("FAIL: vTrack.clips[" + vi + "].remove is not a function");
                }
            } catch (vErr) {
                apiLogs.push("FAIL removing V1 clip " + vi + ": " + vErr.message);
            }
        }
        apiLogs.push("Removed " + vDeleted + " clip(s) from V1");

        var aDeleted = 0;
        if (seq.audioTracks && seq.audioTracks.numTracks > 0) {
            for (var at = 0; at < seq.audioTracks.numTracks; at++) {
                var aTrk = seq.audioTracks[at];
                if (aTrk && aTrk.clips) {
                    for (var ai = aTrk.clips.numItems - 1; ai >= 0; ai--) {
                        try {
                            var acItem = aTrk.clips[ai];
                            if (typeof acItem.remove === "function") {
                                acItem.remove(false, false);
                                aDeleted++;
                            }
                        } catch (aErr) {
                            apiLogs.push("FAIL removing A" + (at + 1) + " clip " + ai + ": " + aErr.message);
                        }
                    }
                }
            }
            apiLogs.push("Removed " + aDeleted + " clip(s) from audio tracks (A1, A2...)");
        }

        // 5. Place segments back-to-back using overwriteClip on the same sequence
        apiLogs.push("Placing " + segmentsToPlace.length + " segments back-to-back with overwriteClip...");
        var tps = 254016000000;
        var currentTimelinePosSec = 0.0;
        var placedCount = 0;

        for (var s = 0; s < segmentsToPlace.length; s++) {
            var seg = segmentsToPlace[s];
            var pItem = seg.projectItem;
            if (!pItem) {
                apiLogs.push("FAIL: segment " + s + " has null projectItem");
                continue;
            }

            // Set inPoint and outPoint in ticks with mediaType=4 (Video + Audio)
            var inTicks = Math.round(seg.srcIn * tps);
            var outTicks = Math.round(seg.srcOut * tps);

            try {
                pItem.setInPoint(String(inTicks), 4);
                pItem.setOutPoint(String(outTicks), 4);
                apiLogs.push("pItem.setInPoint(" + String(inTicks) + ", 4) and setOutPoint(" + String(outTicks) + ", 4) OK");
            } catch (ioErr) {
                apiLogs.push("WARN setting in/out ticks: " + ioErr.message + ". Trying Time object...");
                try {
                    var tInObj = new Time(); tInObj.seconds = seg.srcIn;
                    var tOutObj = new Time(); tOutObj.seconds = seg.srcOut;
                    pItem.setInPoint(tInObj.ticks, 4);
                    pItem.setOutPoint(tOutObj.ticks, 4);
                } catch (ioErr2) {
                    apiLogs.push("FAIL setInPoint/setOutPoint Time object: " + ioErr2.message);
                }
            }

            var tPlace = new Time();
            tPlace.seconds = currentTimelinePosSec;

            var placedOk = false;
            if (typeof seq.overwriteClip === "function") {
                try {
                    seq.overwriteClip(pItem, tPlace, 0, 0);
                    placedOk = true;
                    apiLogs.push("seq.overwriteClip(pItem, " + currentTimelinePosSec.toFixed(2) + "s, 0, 0) succeeded");
                } catch (seqOwErr) {
                    apiLogs.push("seq.overwriteClip error: " + seqOwErr.message + ". Trying track.overwriteClip...");
                }
            }

            if (!placedOk && typeof vTrack.overwriteClip === "function") {
                try {
                    vTrack.overwriteClip(pItem, tPlace);
                    if (aTrack && typeof aTrack.overwriteClip === "function") {
                        aTrack.overwriteClip(pItem, tPlace);
                    }
                    placedOk = true;
                    apiLogs.push("vTrack.overwriteClip(pItem, " + currentTimelinePosSec.toFixed(2) + "s) succeeded");
                } catch (trOwErr) {
                    apiLogs.push("FAIL track.overwriteClip: " + trOwErr.message);
                }
            }

            try { pItem.clearInPoint(); } catch (e) {}
            try { pItem.clearOutPoint(); } catch (e) {}

            if (placedOk) {
                placedCount++;
            }

            currentTimelinePosSec += seg.duration;
        }

        // 6. Verify result on timeline
        var finalVClips = vTrack.clips ? vTrack.clips.numItems : 0;
        var finalAClips = (aTrack && aTrack.clips) ? aTrack.clips.numItems : 0;
        var finalDuration = 0;
        var clipDetails = [];
        var gapDetected = false;

        for (var fc = 0; fc < finalVClips; fc++) {
            var fcClip = vTrack.clips[fc];
            var sSec = parseFloat(fcClip.start.seconds) || 0;
            var eSec = parseFloat(fcClip.end.seconds) || 0;
            clipDetails.push({
                clipIndex: fc,
                name: fcClip.name,
                start: sSec.toFixed(2) + "s",
                end: eSec.toFixed(2) + "s",
                duration: (eSec - sSec).toFixed(2) + "s"
            });
            if (fc > 0) {
                var prevEnd = parseFloat(vTrack.clips[fc - 1].end.seconds) || 0;
                if (Math.abs(sSec - prevEnd) > 0.05) {
                    gapDetected = true;
                    apiLogs.push("WARN: Gap detected between clip " + (fc - 1) + " (end=" + prevEnd.toFixed(2) + "s) and clip " + fc + " (start=" + sSec.toFixed(2) + "s)");
                }
            }
            if (eSec > finalDuration) finalDuration = eSec;
        }

        var gapCheckMsg = gapDetected ? "WARNING: Gaps detected between clips!" : "VERIFIED: Separate contiguous clips with NO GAPS!";
        apiLogs.push("Final V1 clips: " + finalVClips + ", Final duration: " + finalDuration.toFixed(2) + "s");
        apiLogs.push(gapCheckMsg);

        return SC._ok({
            method: "Step (a) Spike: In-Place Cut",
            premiereVersion: ppVer,
            sequenceName: seq.name,
            clipsBeforeCount: vClipsCount,
            clipsAfterCount: finalVClips,
            audioClipsAfterCount: finalAClips,
            origDurationSec: origSeqDur,
            newDurationSec: finalDuration,
            removedRanges: cutRanges,
            placedSegmentsCount: placedCount,
            clipDetails: clipDetails,
            gapCheck: gapCheckMsg,
            apiLogs: apiLogs
        });

    } catch (err) {
        return SC._err("SC_spikeCutInPlace critical error: " + err.message);
    }
}



