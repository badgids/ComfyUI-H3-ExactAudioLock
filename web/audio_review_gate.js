import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_NAME = "H3ExactAudioLockAudioReviewGate";
const EVENT_NAME = "h3_exact_audio_lock.audio_review";
const PENDING_ROUTE = "/h3_exact_audio_lock/audio-review/pending";
const DECISION_ROUTE = "/h3_exact_audio_lock/audio-review/decision";
const queuedReviews = new Map();
const mountedNodes = new Set();
let recoveryPromise = null;

function make(tag, props = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === "className") el.className = value;
        else if (key === "text") el.textContent = value;
        else if (key.startsWith("on") && typeof value === "function") {
            el.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (value !== undefined && value !== null) {
            el.setAttribute(key, String(value));
        }
    }
    for (const child of children) if (child) el.append(child);
    return el;
}

function viewUrl(candidate) {
    const query = new URLSearchParams({
        filename: candidate.filename,
        subfolder: candidate.subfolder,
        type: candidate.type || "temp",
    });
    return api.apiURL(`/view?${query.toString()}`);
}

function nodeId(node) {
    return String(node?.id ?? "");
}

function matchingNode(review) {
    const target = String(review?.node_id ?? "");
    if (target) {
        for (const node of mountedNodes) {
            if (nodeId(node) === target) return node;
        }
        return null;
    }
    return mountedNodes.size === 1 ? mountedNodes.values().next().value : null;
}

function routeReview(review) {
    if (!review || typeof review.token !== "string" || !review.token) return;
    queuedReviews.set(review.token, review);
    const node = matchingNode(review);
    node?._h3AudioReviewHandler?.(review);
}

async function recoverPendingReviews() {
    if (recoveryPromise) return recoveryPromise;
    recoveryPromise = (async () => {
        try {
            const response = await api.fetchApi(PENDING_ROUTE);
            if (!response.ok) return;
            const body = await response.json();
            const liveTokens = new Set();
            for (const review of body.reviews || []) {
                liveTokens.add(review.token);
                routeReview(review);
            }
            for (const token of [...queuedReviews.keys()]) {
                if (!liveTokens.has(token)) queuedReviews.delete(token);
            }
        } catch (error) {
            console.warn("[ComfyUI-H3-ExactAudioLock] Unable to recover pending audio reviews", error);
        } finally {
            recoveryPromise = null;
        }
    })();
    return recoveryPromise;
}

function injectStyles() {
    if (document.getElementById("h3-audio-review-style")) return;
    const style = document.createElement("style");
    style.id = "h3-audio-review-style";
    style.textContent = `
.h3ar-root { box-sizing:border-box; display:flex; flex-direction:column; gap:8px; min-height:390px; padding:10px; overflow:auto; border:1px solid #56637e; border-radius:8px; background:#181a20; color:#e8eaf0; font:12px/1.4 system-ui,sans-serif; }
.h3ar-root * { box-sizing:border-box; }
.h3ar-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.h3ar-title { font-weight:750; color:#a9c2ff; }
.h3ar-badge { color:#d5d9e3; opacity:.78; }
.h3ar-label { font-size:13px; font-weight:650; color:#dfe4ef; }
.h3ar-help,.h3ar-retention,.h3ar-deadline,.h3ar-status,.h3ar-caption,.h3ar-warning,.h3ar-progress { font-size:12px; line-height:1.4; }
.h3ar-help,.h3ar-retention,.h3ar-deadline,.h3ar-progress { color:#aeb5c5; }
.h3ar-nav { display:grid; grid-template-columns:40px minmax(0,1fr) 40px; align-items:center; gap:7px; }
.h3ar-candidate-title { min-width:0; text-align:center; color:#a9c2ff; font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.h3ar-arrow,.h3ar-button { padding:7px; border:1px solid #63708b; border-radius:5px; background:#292e3a; color:#eef1f7; cursor:pointer; }
.h3ar-arrow { font-size:17px; }
.h3ar-arrow:hover,.h3ar-button:hover { background:#343b4b; }
.h3ar-arrow:disabled,.h3ar-button:disabled { opacity:.42; cursor:not-allowed; }
.h3ar-player-panel { padding:8px; border:1px solid #343b4b; border-radius:6px; background:#101218; min-height:82px; }
.h3ar-caption { margin-bottom:5px; color:#c7ccda; }
.h3ar-player { display:block; width:100%; }
.h3ar-warning { margin-top:5px; color:#f2bd67; }
.h3ar-keep { display:flex; align-items:center; gap:6px; color:#d7dbe5; cursor:pointer; }
.h3ar-actions { display:flex; gap:7px; justify-content:flex-end; flex-wrap:wrap; }
.h3ar-button { min-width:160px; font-weight:650; }
.h3ar-reject { border-color:#8a6171; background:#3b252d; }
.h3ar-accept { border-color:#4b9d72; background:#204332; }
.h3ar-status { min-height:34px; padding:7px; border:1px solid #343b4b; border-radius:5px; background:#111319; color:#cbd3e5; white-space:pre-wrap; }
.h3ar-waiting .h3ar-nav,.h3ar-waiting .h3ar-player-panel,.h3ar-waiting .h3ar-keep,.h3ar-waiting .h3ar-progress,.h3ar-waiting .h3ar-retention,.h3ar-waiting .h3ar-deadline,.h3ar-waiting .h3ar-actions { display:none; }
`;
    document.head.append(style);
}

function mount(node) {
    if (node._h3AudioReviewMounted || typeof node.addDOMWidget !== "function") return;
    node._h3AudioReviewMounted = true;
    mountedNodes.add(node);
    injectStyles();

    let current = null;
    let activeIndex = 0;
    let keptIds = new Set();
    let countdownTimer = null;
    let busy = false;

    const root = make("div", { className: "h3ar-root h3ar-waiting" });
    root.tabIndex = 0;
    const head = make("div", { className: "h3ar-head" });
    const title = make("div", { className: "h3ar-title", text: "Audio candidate review" });
    const badge = make("div", { className: "h3ar-badge", text: "waiting" });
    head.append(title, badge);
    const label = make("div", { className: "h3ar-label", text: "Waiting for candidates…" });
    const help = make("div", {
        className: "h3ar-help",
        text: "Review every take, mark alternates to save, then continue with only the selected audio.",
    });
    const nav = make("div", { className: "h3ar-nav" });
    const previous = make("button", { className: "h3ar-arrow", text: "‹", title: "Previous candidate" });
    const candidateTitle = make("div", { className: "h3ar-candidate-title" });
    const next = make("button", { className: "h3ar-arrow", text: "›", title: "Next candidate" });
    nav.append(previous, candidateTitle, next);
    const playerPanel = make("div", { className: "h3ar-player-panel" });
    const keepRow = make("label", { className: "h3ar-keep" });
    const keep = make("input", { type: "checkbox" });
    keepRow.append(keep, document.createTextNode(" Save this take as an alternate"));
    const progress = make("div", { className: "h3ar-progress" });
    const retention = make("div", { className: "h3ar-retention" });
    const deadline = make("div", { className: "h3ar-deadline" });
    const status = make("div", { className: "h3ar-status", text: "Queue a workflow to begin review." });
    const reject = make("button", { className: "h3ar-button h3ar-reject", text: "Reject all & stop" });
    const accept = make("button", { className: "h3ar-button h3ar-accept", text: "Accept selected & continue" });
    const actions = make("div", { className: "h3ar-actions" }, reject, accept);
    const decisionButtons = [reject, accept];
    root.append(head, label, help, nav, playerPanel, keepRow, progress, retention, deadline, status, actions);

    function candidates() {
        return Array.isArray(current?.candidates) ? current.candidates : [];
    }

    function selectedCandidate() {
        return candidates()[activeIndex] ?? null;
    }

    function setBusy(value) {
        busy = Boolean(value);
        decisionButtons.forEach((button) => { button.disabled = busy || !current; });
        previous.disabled = busy || activeIndex <= 0;
        next.disabled = busy || activeIndex >= candidates().length - 1;
        keep.disabled = busy || !current;
    }

    function stopCountdown() {
        if (countdownTimer != null) window.clearInterval(countdownTimer);
        countdownTimer = null;
    }

    function renderWaiting(message = "Queue a workflow to begin review.") {
        stopCountdown();
        current = null;
        activeIndex = 0;
        keptIds = new Set();
        root.classList.add("h3ar-waiting");
        badge.textContent = "waiting";
        label.textContent = "Waiting for candidates…";
        status.textContent = message;
        playerPanel.replaceChildren();
        setBusy(false);
    }

    function updateCountdown() {
        if (!current) return;
        const remaining = Math.max(0, Math.ceil(Number(current.deadline || 0) - Date.now() / 1000));
        deadline.textContent = `Review timeout: ${remaining}s remaining`;
        if (remaining <= 0) {
            status.textContent = "Review timeout reached. The backend will fail this review closed.";
            setBusy(true);
            stopCountdown();
            const expiredToken = current.token;
            window.setTimeout(() => {
                if (current?.token !== expiredToken) return;
                queuedReviews.delete(expiredToken);
                renderWaiting("The previous review expired. Waiting for the next candidate batch.");
                void recoverPendingReviews();
            }, 1200);
        }
    }

    function renderCandidate() {
        const list = candidates();
        const candidate = selectedCandidate();
        playerPanel.replaceChildren();
        if (!candidate) {
            candidateTitle.textContent = "No candidate previews";
            playerPanel.append(make("div", { className: "h3ar-warning", text: "No preview was reported. Check the ComfyUI console." }));
            setBusy(true);
            return;
        }
        badge.textContent = `${activeIndex + 1}/${list.length}`;
        candidateTitle.textContent = `Candidate ${activeIndex + 1} of ${list.length} — ${candidate.label || candidate.candidate_id}`;
        const caption = make("div", {
            className: "h3ar-caption",
            text: `${candidate.sample_rate} Hz${candidate.source_name ? ` · ${candidate.source_name}` : ""}`,
        });
        const player = make("audio", {
            controls: "controls",
            preload: "metadata",
            src: viewUrl(candidate),
            className: "h3ar-player",
        });
        playerPanel.append(caption, player);
        if (candidate.warning) playerPanel.append(make("div", { className: "h3ar-warning", text: candidate.warning }));
        keep.checked = keptIds.has(candidate.candidate_id);
        progress.textContent = `${keptIds.size} take${keptIds.size === 1 ? "" : "s"} marked to save as alternates.`;
        setBusy(busy);
    }

    function renderReview(review) {
        stopCountdown();
        current = review;
        activeIndex = Math.max(0, candidates().length - 1);
        keptIds = new Set();
        busy = false;
        root.classList.remove("h3ar-waiting");
        label.textContent = review.label || "Choose the audio take to continue";
        retention.textContent = review.delete_rejected_audio_files
            ? "Unselected, unkept managed file candidates will be deleted after the decision. Marked alternates are saved under ComfyUI output."
            : "Original file candidates will be retained. Marked alternates are also saved under ComfyUI output.";
        status.textContent = "Listen to the candidates, mark any alternates you want to keep, then accept the active take.";
        renderCandidate();
        updateCountdown();
        countdownTimer = window.setInterval(updateCountdown, 1000);
    }

    async function submit(action) {
        if (!current || busy) return;
        const submittedReview = current;
        const selected = selectedCandidate();
        if (action === "accept" && !selected) return;
        const alternateIds = new Set(keptIds);
        if (selected) alternateIds.delete(selected.candidate_id);
        setBusy(true);
        stopCountdown();
        status.textContent = action === "accept"
            ? "Accepting selected take and saving marked alternates…"
            : "Rejecting this batch and saving any marked alternates…";
        try {
            const response = await api.fetchApi(DECISION_ROUTE, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    token: submittedReview.token,
                    action,
                    selected_candidate_id: action === "accept" ? selected.candidate_id : null,
                    kept_candidate_ids: [...alternateIds],
                }),
            });
            const body = await response.json();
            if (!response.ok || !body.ok) throw new Error(body.error || `HTTP ${response.status}`);
            queuedReviews.delete(submittedReview.token);
            renderWaiting(action === "accept"
                ? "Selection submitted. The workflow is resuming with only that audio candidate."
                : "Batch rejected. This queued execution will stop.");
        } catch (error) {
            if (current?.token !== submittedReview.token) return;
            busy = false;
            setBusy(false);
            status.textContent = `Decision failed: ${error?.message || error}`;
            updateCountdown();
            if (current && Number(current.deadline || 0) > Date.now() / 1000) {
                countdownTimer = window.setInterval(updateCountdown, 1000);
            }
        }
    }

    previous.addEventListener("click", () => {
        if (!busy && activeIndex > 0) { activeIndex -= 1; renderCandidate(); }
    });
    next.addEventListener("click", () => {
        if (!busy && activeIndex < candidates().length - 1) { activeIndex += 1; renderCandidate(); }
    });
    keep.addEventListener("change", () => {
        const candidate = selectedCandidate();
        if (!candidate || busy) return;
        if (keep.checked) keptIds.add(candidate.candidate_id);
        else keptIds.delete(candidate.candidate_id);
        progress.textContent = `${keptIds.size} take${keptIds.size === 1 ? "" : "s"} marked to save as alternates.`;
    });
    reject.addEventListener("click", () => void submit("reject"));
    accept.addEventListener("click", () => void submit("accept"));
    root.addEventListener("keydown", (event) => {
        if (busy) return;
        if (event.key === "ArrowLeft" && activeIndex > 0) {
            event.preventDefault(); activeIndex -= 1; renderCandidate();
        } else if (event.key === "ArrowRight" && activeIndex < candidates().length - 1) {
            event.preventDefault(); activeIndex += 1; renderCandidate();
        }
    });

    node._h3AudioReviewHandler = (review) => {
        const target = String(review?.node_id ?? "");
        if (target && target !== nodeId(node)) return;
        renderReview(review);
    };

    const widget = node.addDOMWidget("h3_audio_review", "h3-audio-review", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 390,
    });
    widget.serialize = false;
    node.setSize?.([Math.max(node.size?.[0] ?? 540, 540), Math.max(node.size?.[1] ?? 500, 500)]);

    const removed = node.onRemoved;
    node.onRemoved = function () {
        stopCountdown();
        mountedNodes.delete(this);
        delete this._h3AudioReviewHandler;
        return removed?.apply(this, arguments);
    };

    for (const review of queuedReviews.values()) {
        if (String(review.node_id ?? "") === nodeId(node) || (!review.node_id && mountedNodes.size === 1)) {
            node._h3AudioReviewHandler(review);
            break;
        }
    }
    window.setTimeout(() => void recoverPendingReviews(), 0);
}

api.addEventListener(EVENT_NAME, (event) => routeReview(event.detail));

app.registerExtension({
    name: "badgids.H3ExactAudioLock.AudioReviewGate",
    async setup() {
        await recoverPendingReviews();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            window.setTimeout(() => mount(this), 0);
            return result;
        };
    },
});
