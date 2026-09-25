import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

const NODE_NAME = "H3ExactAudioLockDialogueReviewBoard";
const EVENT_NAME = "h3_exact_audio_lock.dialogue_review";
const PENDING_ROUTE = "/h3_exact_audio_lock/dialogue-review/pending";
const DECISION_ROUTE = "/h3_exact_audio_lock/dialogue-review/decision";

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

function viewUrl(event) {
    const query = new URLSearchParams({
        filename: event.filename,
        subfolder: event.subfolder,
        type: event.type || "temp",
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
    matchingNode(review)?._h3DialogueReviewHandler?.(review);
}

async function recoverPendingReviews() {
    if (recoveryPromise) return recoveryPromise;
    recoveryPromise = (async () => {
        try {
            const response = await api.fetchApi(PENDING_ROUTE);
            if (!response.ok) return;
            const body = await response.json();
            const live = new Set();
            for (const review of body.reviews || []) {
                live.add(review.token);
                routeReview(review);
            }
            for (const token of [...queuedReviews.keys()]) {
                if (!live.has(token)) queuedReviews.delete(token);
            }
        } catch (error) {
            console.warn("[ComfyUI-H3-ExactAudioLock] dialogue review recovery failed", error);
        } finally {
            recoveryPromise = null;
        }
    })();
    return recoveryPromise;
}

function injectStyles() {
    if (document.getElementById("h3-dialogue-review-style")) return;
    const style = document.createElement("style");
    style.id = "h3-dialogue-review-style";
    style.textContent = `
.h3dr-root{box-sizing:border-box;display:flex;flex-direction:column;gap:8px;min-height:520px;padding:10px;overflow:auto;border:1px solid #56637e;border-radius:8px;background:#181a20;color:#e8eaf0;font:12px/1.35 system-ui,sans-serif}
.h3dr-root *{box-sizing:border-box}.h3dr-head{display:flex;justify-content:space-between;gap:8px;align-items:center}.h3dr-title{font-weight:750;color:#a9c2ff}.h3dr-badge,.h3dr-help,.h3dr-status,.h3dr-deadline{color:#aeb5c5}.h3dr-toolbar{display:flex;gap:6px;flex-wrap:wrap}.h3dr-button{padding:6px 9px;border:1px solid #63708b;border-radius:5px;background:#292e3a;color:#eef1f7;cursor:pointer}.h3dr-button:hover{background:#343b4b}.h3dr-button:disabled{opacity:.42;cursor:not-allowed}.h3dr-commit{border-color:#4b9d72;background:#204332}.h3dr-reject{border-color:#8a6171;background:#3b252d}.h3dr-table{display:flex;flex-direction:column;gap:8px}.h3dr-scene{border:1px solid #343b4b;border-radius:6px;overflow:hidden}.h3dr-scene-title{padding:6px 8px;background:#242936;color:#a9c2ff;font-weight:700}.h3dr-row{display:grid;grid-template-columns:26px 78px 100px minmax(180px,1fr) 86px minmax(180px,260px);gap:7px;align-items:center;padding:7px;border-top:1px solid #2e3441}.h3dr-row:first-of-type{border-top:0}.h3dr-text{white-space:normal}.h3dr-speaker{font-weight:650;color:#dfe4ef}.h3dr-frame{width:78px;padding:4px;background:#101218;color:#eef1f7;border:1px solid #4c566e;border-radius:4px}.h3dr-audio{width:100%;height:32px}.h3dr-warning{grid-column:2/-1;color:#f2bd67}.h3dr-status{min-height:32px;padding:6px;border:1px solid #343b4b;border-radius:5px;background:#111319;white-space:pre-wrap}.h3dr-waiting .h3dr-toolbar,.h3dr-waiting .h3dr-table,.h3dr-waiting .h3dr-deadline{display:none}
`;
    document.head.append(style);
}

function mount(node) {
    if (node._h3DialogueReviewMounted || typeof node.addDOMWidget !== "function") return;
    node._h3DialogueReviewMounted = true;
    mountedNodes.add(node);
    injectStyles();

    let current = null;
    let busy = false;
    let countdownTimer = null;
    const rowState = new Map();

    const root = make("div", { className: "h3dr-root h3dr-waiting" });
    const head = make("div", { className: "h3dr-head" });
    const title = make("div", { className: "h3dr-title", text: "Dialogue review / approval" });
    const badge = make("div", { className: "h3dr-badge", text: "waiting" });
    head.append(title, badge);
    const help = make("div", {
        className: "h3dr-help",
        text: "Review every required line, adjust its raw scene-local start frame if needed, then commit all approved dialogue before H3 generation begins.",
    });
    const toolbar = make("div", { className: "h3dr-toolbar" });
    const approveAll = make("button", { className: "h3dr-button", text: "Approve all" });
    const clearAll = make("button", { className: "h3dr-button", text: "Clear approvals" });
    const reject = make("button", { className: "h3dr-button h3dr-reject", text: "Reject & stop" });
    const commit = make("button", { className: "h3dr-button h3dr-commit", text: "Commit approved dialogue" });
    toolbar.append(approveAll, clearAll, reject, commit);
    const table = make("div", { className: "h3dr-table" });
    const deadline = make("div", { className: "h3dr-deadline" });
    const status = make("div", { className: "h3dr-status", text: "Queue a workflow to begin dialogue review." });
    root.append(head, help, toolbar, table, deadline, status);

    function stopCountdown() {
        if (countdownTimer != null) clearInterval(countdownTimer);
        countdownTimer = null;
    }

    function events() {
        return Array.isArray(current?.events) ? current.events : [];
    }

    function approvedCount() {
        let count = 0;
        for (const value of rowState.values()) if (value.approved) count += 1;
        return count;
    }

    function refreshCommitState() {
        const count = approvedCount();
        const total = events().length;
        badge.textContent = current ? `${count}/${total} approved` : "waiting";
        commit.disabled = busy || !current || total === 0 || count !== total;
        approveAll.disabled = busy || !current;
        clearAll.disabled = busy || !current;
        reject.disabled = busy || !current;
    }

    function updateCountdown() {
        if (!current) return;
        const remaining = Math.max(0, Math.ceil(Number(current.deadline || 0) - Date.now() / 1000));
        deadline.textContent = `Review timeout: ${remaining}s remaining`;
        if (remaining <= 0) {
            busy = true;
            status.textContent = "Review timeout reached. The backend will fail this review closed.";
            refreshCommitState();
            stopCountdown();
        }
    }

    function renderWaiting(message = "Queue a workflow to begin dialogue review.") {
        stopCountdown();
        current = null;
        busy = false;
        rowState.clear();
        table.replaceChildren();
        root.classList.add("h3dr-waiting");
        status.textContent = message;
        refreshCommitState();
    }

    function renderReview(review) {
        current = review;
        busy = false;
        rowState.clear();
        table.replaceChildren();
        root.classList.remove("h3dr-waiting");

        const grouped = new Map();
        for (const event of events()) {
            rowState.set(event.event_id, {
                approved: false,
                startFrame: Number(event.start_frame),
            });
            const key = Number(event.scene_index);
            if (!grouped.has(key)) grouped.set(key, []);
            grouped.get(key).push(event);
        }

        for (const [sceneIndex, sceneEvents] of [...grouped.entries()].sort((a, b) => a[0] - b[0])) {
            const shotId = sceneEvents[0]?.shot_id || `scene_${sceneIndex}`;
            const group = make("div", { className: "h3dr-scene" });
            group.append(make("div", {
                className: "h3dr-scene-title",
                text: `Scene ${sceneIndex} — ${shotId}`,
            }));

            for (const event of sceneEvents) {
                const row = make("div", { className: "h3dr-row" });
                const approve = make("input", { type: "checkbox", title: "Approve this required dialogue event" });
                const line = make("div", { text: event.event_id });
                const speaker = make("div", { className: "h3dr-speaker", text: event.speaker || "—" });
                const text = make("div", { className: "h3dr-text", text: event.text });
                const frame = make("input", {
                    className: "h3dr-frame",
                    type: "number",
                    min: "0",
                    max: String(Math.max(0, Number(event.raw_frames) - Number(event.duration_frames))),
                    step: "1",
                    value: String(event.start_frame),
                    title: `Raw scene-local frame. Delivered content begins after ${event.context_frames} context frame(s).`,
                });
                const audio = make("audio", {
                    className: "h3dr-audio",
                    controls: "controls",
                    preload: "metadata",
                    src: viewUrl(event),
                });

                approve.addEventListener("change", () => {
                    const state = rowState.get(event.event_id);
                    state.approved = approve.checked;
                    refreshCommitState();
                });
                frame.addEventListener("change", () => {
                    const state = rowState.get(event.event_id);
                    state.startFrame = Number(frame.value);
                });

                row.append(approve, line, speaker, text, frame, audio);
                if (event.warning) {
                    row.append(make("div", { className: "h3dr-warning", text: event.warning }));
                }
                group.append(row);
            }
            table.append(group);
        }

        status.textContent = review.label || "Approve every required dialogue event before continuing.";
        updateCountdown();
        countdownTimer = setInterval(updateCountdown, 1000);
        refreshCommitState();
    }

    async function submit(action) {
        if (!current || busy) return;
        const submitted = current;
        const edits = events().map((event) => {
            const state = rowState.get(event.event_id);
            return {
                event_id: event.event_id,
                approved: Boolean(state?.approved),
                start_frame: Number(state?.startFrame),
            };
        });
        if (action === "commit" && edits.some((row) => !row.approved)) {
            status.textContent = "Every required dialogue event must be approved before commit.";
            return;
        }

        busy = true;
        stopCountdown();
        refreshCommitState();
        status.textContent = action === "commit"
            ? "Committing approved dialogue timeline…"
            : "Rejecting dialogue review and stopping this execution…";

        try {
            const response = await api.fetchApi(DECISION_ROUTE, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    token: submitted.token,
                    action,
                    events: action === "commit" ? edits : [],
                }),
            });
            const body = await response.json();
            if (!response.ok || !body.ok) throw new Error(body.error || `HTTP ${response.status}`);
            queuedReviews.delete(submitted.token);
            renderWaiting(action === "commit"
                ? "Dialogue timeline approved. The workflow is resuming into H3 generation."
                : "Dialogue review rejected. This execution will stop.");
        } catch (error) {
            if (current?.token !== submitted.token) return;
            busy = false;
            status.textContent = `Decision failed: ${error?.message || error}`;
            refreshCommitState();
            updateCountdown();
            if (Number(current.deadline || 0) > Date.now() / 1000) {
                countdownTimer = setInterval(updateCountdown, 1000);
            }
        }
    }

    approveAll.addEventListener("click", () => {
        if (busy) return;
        for (const state of rowState.values()) state.approved = true;
        for (const input of table.querySelectorAll('input[type="checkbox"]')) input.checked = true;
        refreshCommitState();
    });
    clearAll.addEventListener("click", () => {
        if (busy) return;
        for (const state of rowState.values()) state.approved = false;
        for (const input of table.querySelectorAll('input[type="checkbox"]')) input.checked = false;
        refreshCommitState();
    });
    reject.addEventListener("click", () => void submit("reject"));
    commit.addEventListener("click", () => void submit("commit"));

    node._h3DialogueReviewHandler = (review) => {
        const target = String(review?.node_id ?? "");
        if (target && target !== nodeId(node)) return;
        renderReview(review);
    };

    const widget = node.addDOMWidget("h3_dialogue_review", "h3-dialogue-review", root, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => 520,
    });
    widget.serialize = false;
    node.setSize?.([Math.max(node.size?.[0] ?? 980, 980), Math.max(node.size?.[1] ?? 650, 650)]);

    const removed = node.onRemoved;
    node.onRemoved = function () {
        stopCountdown();
        mountedNodes.delete(this);
        delete this._h3DialogueReviewHandler;
        return removed?.apply(this, arguments);
    };

    for (const review of queuedReviews.values()) {
        if (String(review.node_id ?? "") === nodeId(node) || (!review.node_id && mountedNodes.size === 1)) {
            node._h3DialogueReviewHandler(review);
            break;
        }
    }
    setTimeout(() => void recoverPendingReviews(), 0);
}

api.addEventListener(EVENT_NAME, (event) => routeReview(event.detail));

app.registerExtension({
    name: "badgids.H3ExactAudioLock.DialogueReviewBoard",
    async setup() {
        await recoverPendingReviews();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE_NAME) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            setTimeout(() => mount(this), 0);
            return result;
        };
    },
});
