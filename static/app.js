/**
 * Amri Maintenance Tracker — Cloud Frontend v4.0
 * Mobile-first card UI with real-time WebSocket sync.
 */

// ── State ─────────────────────────────────────────
let pumps = [], removedPumps = [], authToken = null, currentUser = null;
let ws = null, refreshTimer = null;
const API = '';  // same origin

// ── Init ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    authToken = localStorage.getItem('amt_token');
    if (authToken) { checkAuth(); } else { showLogin(); }
    initTabs();
    document.getElementById('loginPin').addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
    });
    document.getElementById('loginUsername').addEventListener('keydown', e => {
        if (e.key === 'Enter') document.getElementById('loginPin').focus();
    });
});

// ── Auth ──────────────────────────────────────────
function showLogin() {
    document.getElementById('loginScreen').style.display = '';
    document.getElementById('appMain').style.display = 'none';
}

function showApp() {
    document.getElementById('loginScreen').style.display = 'none';
    document.getElementById('appMain').style.display = '';
    document.getElementById('userBadge').textContent = currentUser?.username || '—';
    loadPumps();
    checkConnection();
    startAutoRefresh();
    connectWS();
}

async function doLogin() {
    const u = document.getElementById('loginUsername').value.trim();
    const p = document.getElementById('loginPin').value.trim();
    if (!u || !p) { document.getElementById('loginError').textContent = 'Enter username and PIN'; return; }
    try {
        const res = await fetch(API + '/api/auth/login', {
            method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({username: u, pin: p})
        });
        if (!res.ok) { const e = await res.json(); throw new Error(e.detail || 'Login failed'); }
        const data = await res.json();
        authToken = data.token;
        currentUser = {username: data.username, role: data.role};
        localStorage.setItem('amt_token', authToken);
        document.getElementById('loginError').textContent = '';
        showApp();
    } catch(e) { document.getElementById('loginError').textContent = e.message; }
}

async function checkAuth() {
    try {
        const res = await fetch(API + '/api/auth/me', {headers: authHeaders()});
        if (!res.ok) throw new Error();
        currentUser = await res.json();
        showApp();
    } catch { localStorage.removeItem('amt_token'); authToken = null; showLogin(); }
}

function doLogout() {
    fetch(API + '/api/auth/logout', {method:'POST', headers: authHeaders()}).catch(()=>{});
    localStorage.removeItem('amt_token');
    authToken = null; currentUser = null;
    if (ws) { ws.close(); ws = null; }
    showLogin();
}

function authHeaders() { return {'Authorization': 'Bearer ' + (authToken || '')}; }

function getOperator() { return currentUser?.username || 'Unknown'; }

// ── WebSocket ─────────────────────────────────────
function connectWS() {
    if (ws) ws.close();
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => { document.getElementById('connDot').classList.add('connected'); };
    ws.onclose = () => {
        document.getElementById('connDot').classList.remove('connected');
        setTimeout(connectWS, 3000);
    };
    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'refresh') loadPumps(true);
        } catch {}
    };
}

// ── Tabs ──────────────────────────────────────────
function initTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
            if (btn.dataset.tab === 'history') loadHistory();
            if (btn.dataset.tab === 'removed') loadRemovedPumps();
            if (btn.dataset.tab === 'groups') loadGroupSummary();
            if (btn.dataset.tab === 'settings') loadSettings();
        });
    });
}

// ── Auto refresh ──────────────────────────────────
function startAutoRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => loadPumps(true), 10000);
}

async function checkConnection() {
    try {
        const r = await fetch(API + '/api/server-info');
        if (r.ok) document.getElementById('connDot').classList.add('connected');
    } catch { document.getElementById('connDot').classList.remove('connected'); }
}

// ── API helper ────────────────────────────────────
async function apiPost(url, body) {
    const res = await fetch(API + url, {
        method: 'POST', headers: {'Content-Type':'application/json', ...authHeaders()},
        body: JSON.stringify(body)
    });
    if (!res.ok) { const e = await res.json().catch(()=>({detail:'Failed'})); throw new Error(e.detail); }
    return res.json();
}

async function apiGet(url) {
    const res = await fetch(API + url, {headers: authHeaders()});
    if (!res.ok) throw new Error('Request failed');
    return res.json();
}

// ── Toast ─────────────────────────────────────────
function showToast(msg, type = 'info') {
    const c = document.getElementById('toastContainer');
    const t = document.createElement('div');
    t.className = 'toast ' + type;
    t.textContent = msg;
    c.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3000);
}

// ══════════════════════════════════════════════════
// PUMP DATA
// ══════════════════════════════════════════════════
async function loadPumps(silent = false) {
    try {
        pumps = await apiGet('/api/pumps');
        renderPumps();
        updateStats();
        if (!silent) checkConnection();
    } catch(e) { if (!silent) showToast('Failed to load pumps', 'error'); }
}

function updateStats() {
    document.getElementById('statTotal').textContent = pumps.length;
    document.getElementById('statActive').textContent = pumps.filter(p => p.status === 'Active').length;
    document.getElementById('statStandby').textContent = pumps.filter(p => p.status === 'Standby').length;
    document.getElementById('statDown').textContent = pumps.filter(p => p.status === 'Down').length;
    document.getElementById('statAlerts').textContent = pumps.filter(p =>
        p.alerts.stages !== 'green' || p.alerts.seat_valve !== 'green'
    ).length;
}

// ══════════════════════════════════════════════════
// RENDER PUMP CARDS — mobile-first
// ══════════════════════════════════════════════════
function renderPumps() {
    const c = document.getElementById('pumpContainer');
    if (!pumps.length) { c.innerHTML = '<div class="loading-placeholder">No pumps found</div>'; return; }
    c.innerHTML = pumps.map(p => {
        const a = p.alerts || {};
        const co = p.color_overrides || {};
        const hasAlert = a.stages !== 'green' || a.seat_valve !== 'green';
        const holeHtml = [1,2,3,4,5].map(n => {
            const v = p['hole_'+n+'_count'];
            const clr = a['hole_'+n] || 'green';
            const ov = co['hole_'+n] ? ' color-override' : '';
            return `<div class="pc-hole color-${clr}${ov}" onclick="showColorPicker(event,${p.id},'hole_${n}')">
                <div class="pc-hole-val">${v}</div><div class="pc-hole-lbl">H${n}</div></div>`;
        }).join('');
        const stgOv = co.stages ? ' color-override' : '';
        return `<div class="pump-card${hasAlert?' has-alert':''}" data-id="${p.id}">
            <div class="pc-header">
                <div><span class="pc-name">${esc(p.pump_name)}</span>
                <span class="pc-station">Stn ${p.station} · ${p.model||'-'}</span></div>
                <span class="pc-status pc-status-${p.status}">${p.status}</span>
            </div>
            <div class="pc-metrics">
                <div class="pc-metric color-${a.stages}${stgOv}" onclick="showColorPicker(event,${p.id},'stages')">
                    <div class="pc-metric-value">${p.total_stages}</div><div class="pc-metric-label">Stages</div></div>
                <div class="pc-metric">
                    <div class="pc-metric-value">${p.grease_type}</div><div class="pc-metric-label">Grease</div></div>
                <div class="pc-metric">
                    <div class="pc-metric-value">${p.inspection_date?p.inspection_date.slice(5):'-'}</div>
                    <div class="pc-metric-label">Inspected</div></div>
            </div>
            <div class="pc-holes">${holeHtml}</div>
            ${p.notes?`<div class="pc-info"><span class="pc-info-chip">📝 ${esc(p.notes)}</span></div>`:''}
            <div class="pc-actions">
                <button class="btn btn-success btn-sm" onclick="addStageSingle(${p.id})">+1</button>
                <button class="btn btn-secondary btn-sm" onclick="openEditModal(${p.id})">✏️ Edit</button>
                <button class="btn btn-sm btn-secondary" onclick="movePump(${p.id},'up')">▲</button>
                <button class="btn btn-sm btn-secondary" onclick="movePump(${p.id},'down')">▼</button>
            </div>
        </div>`;
    }).join('');
}

function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ══════════════════════════════════════════════════
// COLOR PICKER
// ══════════════════════════════════════════════════
function showColorPicker(e, pumpId, field) {
    e.stopPropagation();
    document.querySelectorAll('.color-picker-popup').forEach(el=>el.remove());
    const d = document.createElement('div');
    d.className = 'color-picker-popup';
    d.innerHTML = `
        <div class="cp-btn cp-green" onclick="setColor(${pumpId},'${field}','green')"></div>
        <div class="cp-btn cp-yellow" onclick="setColor(${pumpId},'${field}','yellow')"></div>
        <div class="cp-btn cp-red" onclick="setColor(${pumpId},'${field}','red')"></div>
        <div class="cp-btn cp-auto" onclick="setColor(${pumpId},'${field}',null)">Auto</div>`;
    d.style.left = Math.min(e.clientX, window.innerWidth - 200) + 'px';
    d.style.top = (e.clientY - 50) + 'px';
    document.body.appendChild(d);
    setTimeout(() => document.addEventListener('click', function rem() {
        d.remove(); document.removeEventListener('click', rem);
    }, {once:true}), 50);
}

async function setColor(pumpId, field, color) {
    document.querySelectorAll('.color-picker-popup').forEach(el=>el.remove());
    try {
        await apiPost(`/api/pumps/${pumpId}/set-color`, {
            operator_name: getOperator(), field, color
        });
        showToast('Color updated', 'success');
        loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

// ══════════════════════════════════════════════════
// ACTIONS
// ══════════════════════════════════════════════════
async function addStageSingle(id) {
    try {
        await apiPost(`/api/pumps/${id}/add-stage`, {operator_name: getOperator()});
        showToast('+1 stage', 'success'); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

async function addStageAll() {
    try {
        const r = await apiPost('/api/pumps/add-stage-all', {operator_name: getOperator()});
        showToast(`+1 stage to ${r.count} pumps`, 'success'); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

async function addStageActive() {
    try {
        const r = await apiPost('/api/pumps/add-stage-active', {operator_name: getOperator()});
        showToast(`+1 stage to ${r.count} active pumps`, 'success'); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

async function undoLastAction() {
    try {
        const r = await apiPost('/api/undo', {operator_name: getOperator()});
        showToast(r.message, 'success'); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

async function movePump(id, dir) {
    try {
        await apiPost(`/api/pumps/${id}/move-${dir}`, {operator_name: getOperator()});
        loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

async function exportSnapshot() {
    try {
        const res = await fetch(API + `/api/snapshot/save?operator=${getOperator()}`, {headers: authHeaders()});
        const blob = await res.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `amri_snapshot_${new Date().toISOString().slice(0,10)}.json`;
        a.click();
        showToast('Snapshot saved', 'success');
    } catch(e) { showToast('Snapshot failed', 'error'); }
}

// ══════════════════════════════════════════════════
// MODAL SYSTEM
// ══════════════════════════════════════════════════
function openModal(html) {
    document.getElementById('modalContent').innerHTML = html;
    document.getElementById('modalOverlay').classList.add('active');
}
function closeModal() { document.getElementById('modalOverlay').classList.remove('active'); }
document.addEventListener('click', e => {
    if (e.target.id === 'modalOverlay') closeModal();
});

// ── Edit Modal ────────────────────────────────────
function openEditModal(id) {
    const p = pumps.find(x=>x.id===id);
    if (!p) return;
    openModal(`
        <h3>✏️ Edit ${esc(p.pump_name)}</h3>
        <div class="modal-grid">
            <div><label>Status</label><select id="mStatus">
                ${['Active','Standby','Down','Maintenance'].map(s=>`<option${s===p.status?' selected':''}>${s}</option>`).join('')}
            </select></div>
            <div><label>Grease</label><select id="mGrease">
                ${['Oil','Grease'].map(g=>`<option${g===p.grease_type?' selected':''}>${g}</option>`).join('')}
            </select></div>
            <div><label>Stages</label><input type="number" id="mStages" value="${p.total_stages}" min="0"></div>
            <div><label>H1</label><input type="number" id="mH1" value="${p.hole_1_count}" min="0"></div>
            <div><label>H2</label><input type="number" id="mH2" value="${p.hole_2_count}" min="0"></div>
            <div><label>H3</label><input type="number" id="mH3" value="${p.hole_3_count}" min="0"></div>
            <div><label>H4</label><input type="number" id="mH4" value="${p.hole_4_count}" min="0"></div>
            <div><label>H5</label><input type="number" id="mH5" value="${p.hole_5_count}" min="0"></div>
            <div class="modal-grid-wide"><label>Notes</label><input type="text" id="mNotes" value="${esc(p.notes||'')}"></div>
            <div class="modal-grid-wide"><label>Inspection</label><input type="date" id="mInsp" value="${p.inspection_date||''}"></div>
        </div>
        <div class="modal-actions">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" onclick="saveEdit(${id})">💾 Save</button>
            <button class="btn btn-danger btn-sm" onclick="openRemoveConfirm(${id})">🚫</button>
            <button class="btn btn-accent btn-sm" onclick="openReplaceModal(${id})">🔄</button>
        </div>
    `);
}

async function saveEdit(id) {
    try {
        await apiPost(`/api/pumps/${id}/manual-edit`, {
            operator_name: getOperator(),
            status: document.getElementById('mStatus').value,
            grease_type: document.getElementById('mGrease').value,
            total_stages: parseInt(document.getElementById('mStages').value)||0,
            hole_1_count: parseInt(document.getElementById('mH1').value)||0,
            hole_2_count: parseInt(document.getElementById('mH2').value)||0,
            hole_3_count: parseInt(document.getElementById('mH3').value)||0,
            hole_4_count: parseInt(document.getElementById('mH4').value)||0,
            hole_5_count: parseInt(document.getElementById('mH5').value)||0,
            notes: document.getElementById('mNotes').value,
            inspection_date: document.getElementById('mInsp').value || null,
        });
        showToast('Pump updated', 'success'); closeModal(); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

// ── Remove ────────────────────────────────────────
function openRemoveConfirm(id) {
    const p = pumps.find(x=>x.id===id);
    openModal(`<h3>🚫 Remove ${esc(p?.pump_name)}</h3>
        <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px">This pump will be moved to Removed tab.</p>
        <label>Reason</label><input type="text" id="mRemoveReason" placeholder="Reason...">
        <div class="modal-actions">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-danger" onclick="doRemove(${id})">⚠ Remove</button>
        </div>`);
}

async function doRemove(id) {
    try {
        await apiPost(`/api/pumps/${id}/remove`, {
            operator_name: getOperator(), reason: document.getElementById('mRemoveReason').value
        });
        showToast('Pump removed', 'success'); closeModal(); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

// ── Replace ───────────────────────────────────────
function openReplaceModal(id) {
    const p = pumps.find(x=>x.id===id);
    openModal(`<h3>🔄 Replace ${esc(p?.pump_name)}</h3>
        <div class="modal-grid">
            <div class="modal-grid-wide"><label>New Pump Name *</label><input type="text" id="mRepName" placeholder="e.g. HP-30"></div>
            <div><label>Model</label><input type="text" id="mRepModel" placeholder="GD-4"></div>
            <div><label>Status</label><select id="mRepStatus"><option>Active</option><option>Standby</option></select></div>
            <div><label>Stages</label><input type="number" id="mRepStages" value="0" min="0"></div>
            <div><label>Grease</label><select id="mRepGrease"><option>Oil</option><option>Grease</option></select></div>
            <div class="modal-grid-wide"><label>Reason</label><input type="text" id="mRepReason" placeholder="Reason..."></div>
        </div>
        <div class="modal-actions">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-primary" onclick="doReplace(${id})">✓ Replace</button>
        </div>`);
}

async function doReplace(id) {
    try {
        await apiPost(`/api/pumps/${id}/replace`, {
            operator_name: getOperator(),
            new_pump_name: document.getElementById('mRepName').value,
            model: document.getElementById('mRepModel').value,
            status: document.getElementById('mRepStatus').value,
            total_stages: parseInt(document.getElementById('mRepStages').value)||0,
            grease_type: document.getElementById('mRepGrease').value,
            reason: document.getElementById('mRepReason').value,
        });
        showToast('Pump replaced', 'success'); closeModal(); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

// ── Add Pump ──────────────────────────────────────
function openAddPumpDialog() {
    openModal(`<h3>🆕 Add Pump</h3>
        <div class="modal-grid">
            <div class="modal-grid-wide"><label>Pump Name *</label><input type="text" id="mAddName" placeholder="e.g. HP-99"></div>
            <div><label>Model</label><input type="text" id="mAddModel" placeholder="GD-4"></div>
            <div><label>Station #</label><input type="number" id="mAddStation" placeholder="Auto" min="1"></div>
            <div><label>Status</label><select id="mAddStatus"><option>Active</option><option>Standby</option><option>Down</option></select></div>
            <div><label>Grease</label><select id="mAddGrease"><option>Oil</option><option>Grease</option></select></div>
            <div><label>Stages</label><input type="number" id="mAddStages" value="0" min="0"></div>
            <div><label>Inspection</label><input type="date" id="mAddInsp"></div>
        </div>
        <div class="modal-actions">
            <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
            <button class="btn btn-success" onclick="doAddPump()">🆕 Create</button>
        </div>`);
}

async function doAddPump() {
    const name = document.getElementById('mAddName').value.trim();
    if (!name) { showToast('Pump name required', 'error'); return; }
    try {
        await apiPost('/api/pumps', {
            operator_name: getOperator(), pump_name: name,
            model: document.getElementById('mAddModel').value,
            station: parseInt(document.getElementById('mAddStation').value) || null,
            status: document.getElementById('mAddStatus').value,
            grease_type: document.getElementById('mAddGrease').value,
            total_stages: parseInt(document.getElementById('mAddStages').value)||0,
            inspection_date: document.getElementById('mAddInsp').value||null,
        });
        showToast(`${name} added`, 'success'); closeModal(); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

// ══════════════════════════════════════════════════
// REMOVED PUMPS
// ══════════════════════════════════════════════════
async function loadRemovedPumps() {
    try {
        removedPumps = await apiGet('/api/pumps/removed');
        const c = document.getElementById('removedContainer');
        if (!removedPumps.length) { c.innerHTML = '<div class="loading-placeholder">No removed pumps</div>'; return; }
        c.innerHTML = removedPumps.map(p => `
            <div class="removed-card">
                <div class="pc-header">
                    <div><span class="pc-name">${esc(p.pump_name)}</span>
                    <span class="pc-station">Stn ${p.station}</span></div>
                </div>
                <div class="pc-info">
                    <span class="pc-info-chip">Stages: ${p.total_stages}</span>
                    <span class="pc-info-chip">By: ${esc(p.removed_by||'-')}</span>
                    <span class="pc-info-chip">${p.removal_reason||'-'}</span>
                </div>
                <button class="btn btn-success btn-block btn-sm" onclick="restorePump(${p.id})">♻️ Restore</button>
            </div>`).join('');
    } catch { showToast('Failed to load removed', 'error'); }
}

async function restorePump(id) {
    try {
        await apiPost(`/api/pumps/${id}/restore`, {operator_name: getOperator()});
        showToast('Pump restored', 'success'); loadRemovedPumps(); loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

// ══════════════════════════════════════════════════
// HISTORY
// ══════════════════════════════════════════════════
async function loadHistory() {
    try {
        const logs = await apiGet('/api/history?limit=100');
        const c = document.getElementById('historyContainer');
        if (!logs.length) { c.innerHTML = '<div class="loading-placeholder">No history</div>'; return; }
        c.innerHTML = logs.map(h => `
            <div class="history-item${h.undone?' undone':''}">
                <div class="hi-header">
                    <span>${h.timestamp ? new Date(h.timestamp).toLocaleString() : '-'}</span>
                    <span>${esc(h.operator_name)}</span>
                </div>
                <span class="hi-pump">${esc(h.pump_name||'-')}</span> —
                <span class="hi-action">${esc(h.action_type)}</span>
                ${h.comment ? `<div style="font-size:11px;color:var(--text-muted);margin-top:4px">${esc(h.comment)}</div>` : ''}
            </div>`).join('');
    } catch { showToast('Failed to load history', 'error'); }
}

// ══════════════════════════════════════════════════
// GROUPS
// ══════════════════════════════════════════════════
async function loadGroupSummary() {
    try {
        const data = await apiGet('/api/pumps/group-summary');
        const c = document.getElementById('groupContainer');
        const groups = data.groups || {};
        c.innerHTML = Object.entries(groups).map(([name, g]) => `
            <div class="group-card">
                <h4>${esc(name)} (${g.total})</h4>
                <div class="group-stat"><span>Active</span><span>${g.active}</span></div>
                <div class="group-stat"><span>Standby</span><span>${g.standby}</span></div>
                <div class="group-stat"><span>Down</span><span>${g.down}</span></div>
                <div class="group-stat"><span>Total Stages</span><span>${g.total_stages}</span></div>
                <div class="group-stat"><span>Avg Stages</span><span>${g.avg_stages}</span></div>
                <div class="group-stat"><span>Alerts</span><span style="color:${g.alerts?'var(--danger)':'var(--success)'}">${g.alerts}</span></div>
                <div class="group-stat"><span>Balance</span><span>${g.balance}</span></div>
            </div>`).join('');
    } catch { showToast('Failed to load groups', 'error'); }
}

// ══════════════════════════════════════════════════
// SETTINGS
// ══════════════════════════════════════════════════
async function loadSettings() {
    try {
        const s = await apiGet('/api/settings');
        document.getElementById('sStageWarn').value = s.stage_warn_default;
        document.getElementById('sStageCrit').value = s.stage_crit_default;
        document.getElementById('sHoleWarn').value = s.hole_warn_default;
        document.getElementById('sHoleLimit').value = s.hole_limit_default;
    } catch {}
    try {
        const w = await apiGet('/api/well-info');
        document.getElementById('sWellName').value = w.well_name || '';
        document.getElementById('sPadName').value = w.pad_name || '';
        document.getElementById('sRigName').value = w.rig_name || '';
    } catch {}
}

async function saveSettings(applyAll) {
    try {
        await apiPost('/api/settings', {
            operator_name: getOperator(),
            stage_warn_default: parseInt(document.getElementById('sStageWarn').value)||200,
            stage_crit_default: parseInt(document.getElementById('sStageCrit').value)||300,
            hole_warn_default: parseInt(document.getElementById('sHoleWarn').value)||35,
            hole_limit_default: parseInt(document.getElementById('sHoleLimit').value)||45,
            apply_to_all: applyAll
        });
        showToast('Settings saved' + (applyAll ? ' & applied' : ''), 'success');
        loadPumps(true);
    } catch(e) { showToast(e.message, 'error'); }
}

async function saveWellInfo() {
    try {
        await apiPost('/api/well-info', {
            well_name: document.getElementById('sWellName').value,
            pad_name: document.getElementById('sPadName').value,
            rig_name: document.getElementById('sRigName').value,
        });
        showToast('Well info saved', 'success');
    } catch(e) { showToast(e.message, 'error'); }
}
