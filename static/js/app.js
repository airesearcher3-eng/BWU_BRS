/**
 * BRS Automation System — Frontend Logic
 */

// ── Auth State ─────────────────────────────────────────────────
let authToken = localStorage.getItem('brs_token');
let currentUser = null;

function getAuthHeaders() {
    return authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
}

async function authFetch(url, options = {}) {
    const headers = { ...getAuthHeaders(), ...(options.headers || {}) };
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401) {
        doLogout();
        throw new Error('Session expired — please log in again');
    }
    return res;
}

async function readResponseData(response) {
    const text = await response.text();
    if (!text) return null;
    try { return JSON.parse(text); } catch { return { rawText: text }; }
}

function getResponseMessage(data, fallback) {
    if (!data) return fallback;
    return data.detail || data.error || data.message || data.rawText || fallback;
}

// ── Login / Logout ─────────────────────────────────────────────
async function doLogin() {
    const username = document.getElementById('loginUsername').value.trim();
    const password = document.getElementById('loginPassword').value;
    const errEl = document.getElementById('loginError');
    errEl.style.display = 'none';

    if (!username || !password) {
        errEl.textContent = 'Please enter username and password';
        errEl.style.display = 'block';
        return;
    }

    const btn = document.getElementById('loginBtn');
    btn.disabled = true; btn.innerHTML = '<span class="material-symbols-rounded btn-icon">hourglass_top</span> Signing in…';

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Login failed'));

        authToken = data.token;
        currentUser = data.user;
        localStorage.setItem('brs_token', authToken);
        showApp();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
    } finally {
        btn.disabled = false; btn.innerHTML = '<span class="material-symbols-rounded btn-icon">lock</span> Sign In';
    }
}

function doLogout() {
    authToken = null;
    currentUser = null;
    localStorage.removeItem('brs_token');
    document.getElementById('appShell').style.display = 'none';
    document.getElementById('loginPage').style.display = 'flex';
    document.getElementById('loginPassword').value = '';
}

async function showApp() {
    document.getElementById('loginPage').style.display = 'none';
    document.getElementById('appShell').style.display = 'block';

    if (!currentUser) {
        try {
            const res = await authFetch('/api/auth/me');
            const data = await readResponseData(res);
            if (!res.ok) throw new Error('Session invalid');
            currentUser = data;
        } catch {
            doLogout();
            return;
        }
    }

    // Set header user info
    document.getElementById('headerUserName').textContent = currentUser.full_name || currentUser.username;
    const initials = (currentUser.full_name || currentUser.username)
        .split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
    document.getElementById('headerUserAvatar').textContent = initials;

    // Show admin portal link for system_admin
    const isAdmin = currentUser.role === 'system_admin';
    const isMgr = currentUser.role === 'accounts_manager';
    const adminLink = document.getElementById('adminPortalLink');
    const adminSetting = document.getElementById('adminPortalSetting');
    const auditTab = document.getElementById('auditTab');
    if (adminLink) adminLink.style.display = isAdmin ? '' : 'none';
    if (adminSetting) adminSetting.style.display = isAdmin ? '' : 'none';
    if (auditTab) auditTab.style.display = (isAdmin || isMgr) ? '' : 'none';

    const isFC = currentUser.role === 'finance_controller';
    const approvalsTab = document.getElementById('approvalsTab');
    if (approvalsTab) approvalsTab.style.display = (isAdmin || isMgr || isFC) ? '' : 'none';

    // Re-bind tab click handlers
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.onclick = () => switchTab(tab.dataset.tab);
    });

    loadBankAccounts();
    loadDashboard();
}

// ── App State ──────────────────────────────────────────────────
let uploadedFiles = { bankStatement: null, bankBook: null, previousBrs: null };
let currentRunId = null;
let currentExceptionRunId = null;
let currentMatchedReport = null;
let latestRunId = null;
let availableRuns = [];
let allExceptions = [];
let bankAccounts = [];

async function fetchRuns() {
    const res = await authFetch('/api/reconciliation/runs');
    const data = await readResponseData(res);
    if (!res.ok) throw new Error(getResponseMessage(data, 'Failed to load runs'));
    const runs = Array.isArray(data) ? data : [];
    availableRuns = runs;
    latestRunId = runs.length > 0 ? runs[0].id : null;
    populateRunFilters(runs);
    return runs;
}

function populateRunFilters(runs) {
    const runFilter = document.getElementById('excRunFilter');
    if (!runFilter) return;
    const prev = runFilter.value;
    runFilter.innerHTML = '<option value="">Latest Run</option>' + runs.map(r =>
        `<option value="${r.id}">Run #${r.id} • ${r.period_start||'—'} → ${r.period_end||'—'}</option>`
    ).join('');
    if (currentExceptionRunId && runs.some(r => r.id === currentExceptionRunId))
        runFilter.value = String(currentExceptionRunId);
    else if (prev && runs.some(r => String(r.id) === prev))
        runFilter.value = prev;
}

// ── Tab Navigation ─────────────────────────────────────────────
function switchTab(tabId) {
    document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    const tab = document.querySelector(`.nav-tab[data-tab="${tabId}"]`);
    const content = document.getElementById(tabId);
    if (tab) tab.classList.add('active');
    if (content) content.classList.add('active');
    if (tabId === 'dashboard') loadDashboard();
    if (tabId === 'exceptions') loadExceptions();
    if (tabId === 'reports') loadReports();
    if (tabId === 'audit') loadAuditLog();
    if (tabId === 'approvals') loadApprovals();
    if (tabId === 'admin') loadUsers();
}

// ── File Upload ────────────────────────────────────────────────
function setupUpload(areaId, inputId, type) {
    const area = document.getElementById(areaId);
    const input = document.getElementById(inputId);
    if (!area || !input) return;
    area.addEventListener('click', () => input.click());
    area.addEventListener('dragover', e => { e.preventDefault(); area.classList.add('dragover'); });
    area.addEventListener('dragleave', () => area.classList.remove('dragover'));
    area.addEventListener('drop', e => {
        e.preventDefault(); area.classList.remove('dragover');
        if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0], type, area);
    });
    input.addEventListener('change', () => { if (input.files.length) handleFile(input.files[0], type, area); });
}

function handleFile(file, type, area) {
    uploadedFiles[type] = file;
    area.classList.add('has-file');
    area.innerHTML = `<div class="upload-icon">✅</div><div class="upload-title">${file.name}</div><div class="upload-desc">${(file.size/1024).toFixed(1)} KB — Click to replace</div>`;
}

// ── Reconciliation ─────────────────────────────────────────────
async function startReconciliation() {
    if (!uploadedFiles.bankStatement || !uploadedFiles.bankBook) {
        showAlert('Please upload both bank statement and bank book files', 'danger');
        return;
    }
    currentMatchedReport = null;
    const btn = document.getElementById('startReconciliationBtn');
    btn.disabled = true; btn.innerHTML = '<span class="material-symbols-rounded btn-icon">hourglass_top</span> Processing…';
    const progressCard = document.getElementById('progressCard');
    const resultsCard = document.getElementById('resultsCard');
    progressCard.style.display = 'block'; resultsCard.style.display = 'none';

    try {
        updateProgress('Uploading bank statement...', 10);
        const bsPath = await uploadFile(uploadedFiles.bankStatement, 'bank-statement');
        updateProgress('Uploading bank book...', 25);
        const bbPath = await uploadFile(uploadedFiles.bankBook, 'bank-book');
        let prevBrsPath = null;
        if (uploadedFiles.previousBrs) {
            updateProgress('Uploading previous BRS...', 35);
            prevBrsPath = await uploadFile(uploadedFiles.previousBrs, 'previous-brs');
        }
        updateProgress('Running matching engine...', 50);
        const bankAccountId = document.getElementById('bankAccountSelect').value;
        const useRag = document.getElementById('ragModeToggle')?.checked || false;
        if (useRag) updateProgress('Running RAG matching engine (AI)...', 50);
        else updateProgress('Running matching engine...', 50);
        const result = await authFetch('/api/reconciliation/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                period_start: document.getElementById('periodStart').value,
                period_end: document.getElementById('periodEnd').value,
                bank_statement_path: bsPath,
                bank_book_path: bbPath,
                previous_brs_path: prevBrsPath,
                bank_account_id: bankAccountId ? parseInt(bankAccountId, 10) : null,
                use_rag: useRag,
            }),
        });
        const data = await readResponseData(result);
        if (!result.ok) throw new Error(getResponseMessage(data, 'Reconciliation failed'));
        updateProgress('Generating BRS output...', 85);
        await new Promise(r => setTimeout(r, 500));
        updateProgress('Complete!', 100);
        currentRunId = data.run_id;
        setTimeout(() => { progressCard.style.display = 'none'; showResults(data); }, 800);
    } catch (err) {
        progressCard.style.display = 'none';
        showAlert(`Error: ${err.message}`, 'danger');
    } finally {
        btn.disabled = false; btn.innerHTML = '<span class="material-symbols-rounded btn-icon">bolt</span> Start Reconciliation';
    }
}

async function uploadFile(file, type) {
    const formData = new FormData();
    formData.append('file', file);
    const res = await authFetch(`/api/upload/${type}`, { method: 'POST', body: formData });
    const data = await readResponseData(res);
    if (!res.ok) throw new Error(getResponseMessage(data, 'Upload failed'));
    return data.filepath;
}

function updateProgress(message, percent) {
    document.getElementById('progressMessage').textContent = message;
    document.getElementById('progressFill').style.width = percent + '%';
    document.getElementById('progressPercent').textContent = percent + '%';
}

function showResults(data) {
    const card = document.getElementById('resultsCard');
    card.style.display = 'block';
    currentRunId = data.run_id || currentRunId;
    currentExceptionRunId = currentRunId;
    document.getElementById('successAlert').style.display = 'flex';
    document.getElementById('resultsSummary').innerHTML = `
        <div class="summary-item"><div class="summary-label">Bank Statement</div><div class="summary-value">${data.total_bank_stmt}</div></div>
        <div class="summary-item"><div class="summary-label">Bank Book</div><div class="summary-value">${data.total_bank_book}</div></div>
        <div class="summary-item success"><div class="summary-label">Total Matched</div><div class="summary-value">${data.total_matched}</div></div>
        <div class="summary-item danger"><div class="summary-label">Unmatched</div><div class="summary-value">${data.total_unmatched}</div></div>
        <div class="summary-item"><div class="summary-label">Match Rate</div><div class="summary-value">${data.auto_match_rate}%</div></div>
        <div class="summary-item"><div class="summary-label">Carry Forward</div><div class="summary-value">${data.carry_forward || 0}</div></div>
    `;
    const passDescs = { 1:'Exact Reference Match', 2:'One-to-Many Aggregated', 3:'Rule-Based (GIB/Tax)', 4:'FD & Contra', 5:'RAG AI Matching' };
    const passBody = document.getElementById('passBreakdown');
    passBody.innerHTML = '';
    for (const [pass, count] of Object.entries(data.pass_counts)) {
        passBody.innerHTML += `<tr><td><span class="badge badge-info">Pass ${pass}</span></td><td>${passDescs[pass]||'Other'}</td><td><strong>${count}</strong></td></tr>`;
    }
    document.getElementById('downloadBrsBtn').onclick = () => {
        const a = document.createElement('a');
        a.href = `/api/reconciliation/run/${data.run_id}/download`;
        a.download = `BRS_run_${data.run_id}.xlsx`;
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
    };
    document.getElementById('viewMatchedReportBtn').onclick = () => openMatchedReport(data.run_id);

    // BRS Statement Summary
    const brsSection = document.getElementById('brsSummarySection');
    const brsBody = document.getElementById('brsSummaryBody');
    if (data.totals && data.section_summary) {
        const fmt = v => Number(v).toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2});
        const ss = data.section_summary;
        const t = data.totals;
        const cfLabel = data.carry_forward ? ` <span class="badge badge-info">${data.carry_forward} carry-forward</span>` : '';
        brsBody.innerHTML = `
            <tr><td><strong>Balance as per Bank Book</strong></td><td style="text-align:right"><strong>₹${fmt(t.bank_book_balance)}</strong></td></tr>
            <tr><td>Add: Cheque issued but not debited by Bank (${ss.add_cheque_issued?.count||0} items)</td><td style="text-align:right">₹${fmt(t.add_cheque_issued||0)}</td></tr>
            <tr><td>Add: Amount credited by Bank not in Book (${ss.add_bank_credit?.count||0} items)</td><td style="text-align:right">₹${fmt(t.add_bank_credit||0)}</td></tr>
            <tr><td>Less: Cheque deposited but not credited (${ss.less_cheque_deposit?.count||0} items)</td><td style="text-align:right">₹${fmt(t.less_cheque_deposit||0)}</td></tr>
            <tr><td>Less: Amount debited by Bank not in Book (${ss.less_bank_debit?.count||0} items)</td><td style="text-align:right">₹${fmt(t.less_bank_debit||0)}</td></tr>
            <tr style="border-top:2px solid var(--border-color)"><td><strong>Reconciled Balance</strong></td><td style="text-align:right"><strong>₹${fmt(t.reconciled_balance)}</strong></td></tr>
            <tr><td><strong>Balance as per Bank Statement</strong></td><td style="text-align:right"><strong>₹${fmt(t.bank_statement_balance)}</strong></td></tr>
            <tr style="border-top:2px solid var(--border-color)"><td><strong>Difference</strong>${cfLabel}</td><td style="text-align:right"><strong style="color:${Number(t.difference)===0?'var(--success-color)':'var(--danger-color)'}">₹${fmt(t.difference)}</strong></td></tr>
        `;
        brsSection.style.display = 'block';
    } else {
        brsSection.style.display = 'none';
    }
}

// ── Dashboard ──────────────────────────────────────────────────
async function loadDashboard() {
    try {
        const runs = await fetchRuns();
        if (runs.length > 0) {
            const latest = runs[0];
            const rate = latest.total_bank_stmt_entries > 0
                ? ((latest.total_matched / latest.total_bank_stmt_entries) * 100).toFixed(1) : '—';
            document.getElementById('statMatchRate').textContent = rate !== '—' ? rate + '%' : '—';
            document.getElementById('statRuns').textContent = runs.length;
            document.getElementById('statUnmatched').textContent = latest.total_unmatched || 0;
        }
        const excRes = await authFetch('/api/exceptions?status=open');
        const excData = await readResponseData(excRes);
        if (!excRes.ok) throw new Error(getResponseMessage(excData, 'Failed'));
        document.getElementById('statExceptions').textContent = (Array.isArray(excData) ? excData : []).length;

        const tbody = document.getElementById('runsTableBody');
        if (runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8"><div class="empty-state"><div class="empty-state-icon">📋</div><div class="empty-state-text">No reconciliation runs yet</div></div></td></tr>';
            return;
        }
        tbody.innerHTML = runs.map(r => {
            const matchRate = r.total_bank_stmt_entries > 0
                ? `${r.total_matched} (${((r.total_matched/r.total_bank_stmt_entries)*100).toFixed(0)}%)` : '—';
            return `<tr>
                <td>${formatDateTime(r.created_at)}</td><td>${r.period_start||''} → ${r.period_end||''}</td>
                <td>${r.total_bank_stmt_entries||0}</td><td>${r.total_bank_book_entries||0}</td>
                <td>${matchRate}</td><td>${r.total_unmatched||0}</td>
                <td>${getStatusBadge(r.status)}</td>
                <td>
                    <button class="btn btn-secondary btn-sm" onclick="viewRunDetail(${r.id})">View</button>
                    ${r.brs_output_path ? `<button class="btn btn-secondary btn-sm" onclick="window.open('/api/reconciliation/run/${r.id}/download','_blank')">📥</button>` : ''}
                </td>
            </tr>`;
        }).join('');
    } catch (err) { console.error('Dashboard:', err); }
}

// ── Exceptions ─────────────────────────────────────────────────
async function loadExceptions(runId = null) {
    try {
        if (!availableRuns.length) await fetchRuns();
        if (runId !== null && runId !== undefined) currentExceptionRunId = runId ? parseInt(runId, 10) : null;
        const effectiveRunId = currentExceptionRunId || currentRunId || latestRunId;
        const runFilter = document.getElementById('excRunFilter');
        if (runFilter) runFilter.value = currentExceptionRunId ? String(currentExceptionRunId) : '';

        const url = effectiveRunId ? `/api/exceptions?run_id=${encodeURIComponent(effectiveRunId)}` : '/api/exceptions';
        const res = await authFetch(url);
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        allExceptions = Array.isArray(data) ? data : [];
        renderExceptions(allExceptions);
        const openCount = allExceptions.filter(e => e.status === 'open' || e.status === 'escalated').length;
        document.getElementById('excCountBadge').textContent = effectiveRunId
            ? `${openCount} Pending • Run #${effectiveRunId}` : `${openCount} Pending`;
    } catch (err) { console.error('Exceptions:', err); }
}

function filterExceptions() {
    const search = document.getElementById('excSearch').value.toLowerCase();
    const status = document.getElementById('excStatusFilter').value;
    const type = document.getElementById('excTypeFilter').value;
    let filtered = allExceptions;
    if (search) filtered = filtered.filter(e => (e.narration||'').toLowerCase().includes(search) || (e.description||'').toLowerCase().includes(search));
    if (status) filtered = filtered.filter(e => e.status === status);
    if (type) filtered = filtered.filter(e => e.exception_type === type);
    renderExceptions(filtered);
}

function renderExceptions(exceptions) {
    const container = document.getElementById('exceptionsList');
    if (exceptions.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">✅</div><div class="empty-state-text">No exceptions found</div></div>';
        return;
    }
    container.innerHTML = exceptions.map(e => {
        const statusBadge = getStatusBadge(e.status);
        const typeBadge = getExcTypeBadge(e.exception_type);
        const amount = formatAmount(e.amount);
        const escalatedInfo = e.status === 'escalated' && e.assigned_to
            ? `<div style="margin-top:0.25rem"><span class="badge badge-danger">⬆ Escalated to: ${escapeHtml(e.assigned_to_name || 'Manager')}</span></div>` : '';
        const solution = e.suggested_solution
            ? `<div class="exc-solution"><strong>💡 Suggested Action:</strong> ${escapeHtml(truncate(e.suggested_solution, 150))}</div>` : '';

        return `
            <div class="exception-item">
                <div class="exception-header">
                    <div>
                        <strong>Exception #${e.id}</strong> ${statusBadge} ${typeBadge}
                        ${escalatedInfo}
                    </div>
                    <div style="font-weight:600;color:var(--text-primary)">₹${amount}</div>
                </div>
                <div class="exception-details">
                    <div><strong>Date:</strong> ${e.transaction_date||'—'} &nbsp;|&nbsp; <strong>Section:</strong> ${formatSection(e.brs_section)}</div>
                    <div><strong>Source:</strong> ${e.source === 'bank_statement' ? '🏦 Bank Statement' : '📒 Bank Book'} &nbsp;|&nbsp; <strong>Direction:</strong> ${e.direction === 'IN' ? '↓ Credit' : '↑ Debit'}</div>
                    <div><strong>Description:</strong> ${escapeHtml(truncate(e.narration || e.description || '—', 120))}</div>
                    ${e.voucher_type ? `<div><strong>Voucher:</strong> ${escapeHtml(e.voucher_type)} ${e.voucher_no ? '#'+escapeHtml(e.voucher_no) : ''}</div>` : ''}
                    ${e.cheque_no ? `<div><strong>Cheque No:</strong> ${escapeHtml(e.cheque_no)}</div>` : ''}
                    ${solution}
                </div>
                <div class="action-buttons">
                    <button class="btn btn-secondary btn-sm" onclick="openExcDetail(${e.id})">🔍 Details</button>
                    ${e.status !== 'resolved' ? `
                        <button class="btn btn-primary btn-sm" onclick="resolveException(${e.id})">✓ Resolve</button>
                        <button class="btn btn-secondary btn-sm" onclick="openCommentModal(${e.id})">💬 Comment</button>
                        <button class="btn btn-danger btn-sm" onclick="escalateException(${e.id})">⬆ Escalate</button>
                    ` : '<span class="badge badge-success">Resolved</span>'}
                </div>
            </div>
        `;
    }).join('');
}

async function openExcDetail(excId) {
    const container = document.getElementById('excDetailContent');
    container.innerHTML = '<div class="spinner"></div>';
    openModal('excDetailModal');
    try {
        const res = await authFetch(`/api/exceptions/${excId}`);
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        container.innerHTML = `
            <div class="summary-grid" style="grid-template-columns:repeat(3,1fr)">
                <div class="summary-item"><div class="summary-label">Amount</div><div class="summary-value">₹${formatAmount(data.amount)}</div></div>
                <div class="summary-item"><div class="summary-label">Direction</div><div class="summary-value">${data.direction==='IN'?'↓ Credit':'↑ Debit'}</div></div>
                <div class="summary-item"><div class="summary-label">Status</div><div class="summary-value">${getStatusBadge(data.status)}</div></div>
            </div>
            <div class="exc-detail-grid">
                <div class="exc-detail-row"><span class="exc-detail-label">Exception Type</span><span>${getExcTypeBadge(data.exception_type)} ${escapeHtml(data.exception_type)}</span></div>
                <div class="exc-detail-row"><span class="exc-detail-label">BRS Section</span><span>${formatSection(data.brs_section)}</span></div>
                <div class="exc-detail-row"><span class="exc-detail-label">Transaction Date</span><span>${data.transaction_date||'—'}</span></div>
                <div class="exc-detail-row"><span class="exc-detail-label">Source</span><span>${data.source==='bank_statement'?'🏦 Bank Statement':'📒 Bank Book'}</span></div>
                <div class="exc-detail-row"><span class="exc-detail-label">Narration</span><span>${escapeHtml(data.narration||'—')}</span></div>
                <div class="exc-detail-row"><span class="exc-detail-label">Description</span><span>${escapeHtml(data.description||'—')}</span></div>
                ${data.voucher_type?`<div class="exc-detail-row"><span class="exc-detail-label">Voucher</span><span>${escapeHtml(data.voucher_type)} ${data.voucher_no? '#'+escapeHtml(data.voucher_no):''}</span></div>`:''}
                ${data.cheque_no?`<div class="exc-detail-row"><span class="exc-detail-label">Cheque No</span><span>${escapeHtml(data.cheque_no)}</span></div>`:''}
                ${data.transaction_id?`<div class="exc-detail-row"><span class="exc-detail-label">Transaction ID</span><span>${escapeHtml(data.transaction_id)}</span></div>`:''}
                ${data.assigned_to_name?`<div class="exc-detail-row"><span class="exc-detail-label">Assigned To</span><span><span class="badge badge-info">${escapeHtml(data.assigned_to_name)}</span></span></div>`:''}
                <div class="exc-detail-row"><span class="exc-detail-label">SLA Days</span><span>${data.sla_days||3} business days</span></div>
            </div>
            <div class="exc-solution-box">
                <div class="exc-solution-title">💡 Suggested Solution</div>
                <div>${escapeHtml(data.suggested_solution||'Review manually.')}</div>
            </div>
            ${data.comments && data.comments.length > 0 ? `
                <div style="margin-top:1rem"><strong>Comments (${data.comments.length}):</strong></div>
                ${data.comments.map(c => `
                    <div class="audit-item" style="margin-top:0.5rem">
                        <div class="audit-timestamp">${formatDateTime(c.created_at)} ${c.commenter_name?'• '+escapeHtml(c.commenter_name):''}</div>
                        <div class="audit-action">${escapeHtml(c.comment_text)}</div>
                    </div>
                `).join('')}
            ` : '<div style="margin-top:1rem;color:var(--text-tertiary)">No comments yet.</div>'}
        `;
    } catch (err) {
        container.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.message)}</div>`;
    }
}

async function resolveException(excId) {
    if (!confirm('Are you sure you want to resolve this exception?')) return;
    try {
        const res = await authFetch(`/api/exceptions/${excId}/resolve`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ resolution_type: 'manual_match' }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        loadExceptions();
    } catch (err) { showAlert(err.message, 'danger'); }
}

async function escalateException(excId) {
    try {
        const res = await authFetch(`/api/exceptions/${excId}/escalate`, { method: 'POST' });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        showAlert(data.message || 'Exception escalated', 'success');
        loadExceptions();
    } catch (err) { showAlert(err.message, 'danger'); }
}

function openCommentModal(excId) {
    document.getElementById('commentExcId').value = excId;
    document.getElementById('commentText').value = '';
    openModal('commentModal');
}

async function submitComment() {
    const excId = document.getElementById('commentExcId').value;
    const text = document.getElementById('commentText').value.trim();
    if (!text) return;
    try {
        const res = await authFetch(`/api/exceptions/${excId}/comment`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comment: text }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        closeModal('commentModal');
        showAlert('Comment added', 'success');
    } catch (err) { showAlert(err.message, 'danger'); }
}

// ── Approval ───────────────────────────────────────────────────
async function submitForApproval() {
    if (!currentRunId) return;
    try {
        const res = await authFetch(`/api/approval/${currentRunId}/submit`, { method: 'POST' });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        showAlert(data.message, 'success');
    } catch (err) { showAlert(`Error: ${err.message}`, 'danger'); }
}

// ── Approvals & Escalated Exceptions Tab ───────────────────────
async function loadApprovals() {
    await Promise.all([loadPendingApprovals(), loadEscalatedExceptions()]);
}

async function loadPendingApprovals() {
    const container = document.getElementById('pendingApprovalsList');
    if (!container) return;
    try {
        const runs = await fetchRuns();
        const role = currentUser ? currentUser.role : '';
        let pending = [];
        if (role === 'accounts_manager' || role === 'system_admin') {
            pending = runs.filter(r => r.status === 'pending_review');
        } else if (role === 'finance_controller') {
            pending = runs.filter(r => r.status === 'approved');
        }
        if (pending.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon"><span class="material-symbols-rounded" style="font-size:2rem">check_circle</span></div><div class="empty-state-text">No runs pending your approval</div></div>';
            return;
        }
        container.innerHTML = `<div class="table-container"><table><thead><tr><th>Run #</th><th>Period</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead><tbody>${
            pending.map(r => {
                const period = (r.period_start || '—') + ' to ' + (r.period_end || '—');
                const statusBadge = getRunStatusBadge(r.status);
                let actions = '';
                if ((role === 'accounts_manager' || role === 'system_admin') && r.status === 'pending_review') {
                    actions = `<button class="btn btn-primary btn-sm" onclick="approveRun(${r.id})"><span class="material-symbols-rounded btn-icon">check</span> Approve</button>`;
                } else if (role === 'finance_controller' && r.status === 'approved') {
                    actions = `<button class="btn btn-primary btn-sm" onclick="signOffRun(${r.id})"><span class="material-symbols-rounded btn-icon">verified</span> Sign Off</button>`;
                }
                actions += ` <button class="btn btn-secondary btn-sm" onclick="viewRunReport(${r.id})"><span class="material-symbols-rounded btn-icon">description</span> View</button>`;
                return `<tr><td><strong>#${r.id}</strong></td><td>${escapeHtml(period)}</td><td>${statusBadge}</td><td style="font-size:0.82rem;color:var(--text-tertiary)">${formatDateTime(r.created_at)}</td><td>${actions}</td></tr>`;
            }).join('')
        }</tbody></table></div>`;
    } catch (err) { container.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.message)}</div>`; }
}

async function loadEscalatedExceptions() {
    const container = document.getElementById('escalatedExceptionsList');
    const badge = document.getElementById('escalatedCountBadge');
    if (!container) return;
    try {
        const res = await authFetch('/api/exceptions?status=escalated');
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        const escalated = Array.isArray(data) ? data : [];
        if (badge) badge.textContent = String(escalated.length);
        if (escalated.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon"><span class="material-symbols-rounded" style="font-size:2rem">check_circle</span></div><div class="empty-state-text">No escalated exceptions</div></div>';
            return;
        }
        container.innerHTML = escalated.map(e => {
            const typeBadge = getExcTypeBadge(e.exception_type);
            const amount = formatAmount(e.amount);
            return `
                <div class="exception-item">
                    <div class="exception-header">
                        <div>
                            <strong>Exception #${e.id}</strong> <span class="badge badge-danger">Escalated</span> ${typeBadge}
                            <div style="margin-top:0.15rem;font-size:0.82rem;color:var(--text-secondary)">Run #${e.run_id} &nbsp;|&nbsp; ${e.transaction_date||'—'} &nbsp;|&nbsp; ${formatSection(e.brs_section)}</div>
                        </div>
                        <div style="font-weight:600;color:var(--text-primary)">\u20B9${amount}</div>
                    </div>
                    <div class="exception-details">
                        <div><strong>Description:</strong> ${escapeHtml(truncate(e.narration || e.description || '\u2014', 150))}</div>
                        ${e.assigned_to_name ? `<div><strong>Assigned to:</strong> ${escapeHtml(e.assigned_to_name)}</div>` : ''}
                    </div>
                    <div class="action-buttons">
                        <button class="btn btn-secondary btn-sm" onclick="openExcDetail(${e.id})"><span class="material-symbols-rounded btn-icon">search</span> Details</button>
                        <button class="btn btn-primary btn-sm" onclick="resolveException(${e.id})"><span class="material-symbols-rounded btn-icon">check</span> Resolve</button>
                        <button class="btn btn-secondary btn-sm" onclick="openCommentModal(${e.id})"><span class="material-symbols-rounded btn-icon">comment</span> Comment</button>
                    </div>
                </div>`;
        }).join('');
    } catch (err) { container.innerHTML = `<div class="alert alert-danger">${escapeHtml(err.message)}</div>`; }
}

function getRunStatusBadge(status) {
    const map = {
        'running': '<span class="badge badge-info">Running</span>',
        'completed': '<span class="badge badge-success">Completed</span>',
        'failed': '<span class="badge badge-danger">Failed</span>',
        'pending_review': '<span class="badge badge-warning">Pending Review</span>',
        'approved': '<span class="badge badge-info">Approved</span>',
        'signed_off': '<span class="badge badge-success">Signed Off</span>',
    };
    return map[status] || `<span class="badge">${escapeHtml(status)}</span>`;
}

async function approveRun(runId) {
    const comments = prompt('Approval comments (optional):') || '';
    try {
        const res = await authFetch(`/api/approval/${runId}/approve`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comments }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        showAlert(data.message || 'Run approved', 'success');
        loadApprovals();
    } catch (err) { showAlert(err.message, 'danger'); }
}

async function signOffRun(runId) {
    const comments = prompt('Sign-off comments (optional):') || '';
    try {
        const res = await authFetch(`/api/approval/${runId}/signoff`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ comments }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        showAlert(data.message || 'Run signed off', 'success');
        loadApprovals();
    } catch (err) { showAlert(err.message, 'danger'); }
}

function viewRunReport(runId) {
    switchTab('reports');
}

function viewExceptions() {
    currentExceptionRunId = currentRunId || currentExceptionRunId || latestRunId;
    switchTab('exceptions');
}

// ── Reports ────────────────────────────────────────────────────
async function loadReports() {
    try {
        const runs = await fetchRuns();
        const tbody = document.getElementById('reportsTableBody');
        if (runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state"><div class="empty-state-icon">📁</div><div class="empty-state-text">No reports yet</div></div></td></tr>';
            renderMatchedReport(null); return;
        }
        tbody.innerHTML = runs.map(r => {
            const rate = r.total_bank_stmt_entries > 0 ? ((r.total_matched/r.total_bank_stmt_entries)*100).toFixed(1)+'%' : '—';
            return `<tr><td>#${r.id}</td><td>${r.period_start} → ${r.period_end}</td><td>${rate}</td>
                <td>${getStatusBadge(r.status)}</td><td>${formatDateTime(r.created_at)}</td>
                <td>${r.brs_output_path ? `<button class="btn btn-primary btn-sm" onclick="downloadBrs(${r.id})">📥 BRS</button>` : '—'}
                <button class="btn btn-secondary btn-sm" onclick="openMatchedReport(${r.id})">Matched</button>
                <button class="btn btn-primary btn-sm" onclick="downloadMatchedExcel(${r.id})">📥 Excel</button>
                <button class="btn btn-secondary btn-sm" onclick="printMatchedReport(${r.id})">Print</button></td></tr>`;
        }).join('');
        const reportRunId = currentRunId || latestRunId;
        if (reportRunId && (!currentMatchedReport || currentMatchedReport.run_id !== reportRunId))
            await loadMatchedReport(reportRunId, { switchToReports: false });
    } catch (err) { console.error('Reports:', err); }
}

async function openMatchedReport(runId = null) {
    const target = runId || currentRunId || latestRunId;
    if (!target) { showAlert('No run available', 'danger'); return; }
    switchTab('reports');
    await loadMatchedReport(target, { switchToReports: false });
}

async function loadMatchedReport(runId, options = {}) {
    if (options.switchToReports !== false) switchTab('reports');
    try {
        const res = await authFetch(`/api/reconciliation/run/${runId}/matches`);
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        currentRunId = runId; currentMatchedReport = data;
        renderMatchedReport(data);
    } catch (err) { renderMatchedReport(null); showAlert(err.message, 'danger'); }
}

function renderMatchedReport(report) {
    const badge = document.getElementById('matchedReportBadge');
    const printBtn = document.getElementById('printMatchedBtn');
    const summary = document.getElementById('matchedReportSummary');
    const tbody = document.getElementById('matchedReportBody');

    if (!report) {
        currentMatchedReport = null; badge.textContent = 'No run selected'; printBtn.disabled = true;
        const excelBtnNull = document.getElementById('downloadMatchedExcelBtn');
        if (excelBtnNull) excelBtnNull.disabled = true;
        summary.innerHTML = '<div class="summary-item"><div class="summary-label">Matched Groups</div><div class="summary-value">0</div></div><div class="summary-item"><div class="summary-label">Statement Entries</div><div class="summary-value">0</div></div><div class="summary-item"><div class="summary-label">Ledger Entries</div><div class="summary-value">0</div></div><div class="summary-item"><div class="summary-label">Matched Amount</div><div class="summary-value">0.00</div></div>';
        tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state"><div class="empty-state-icon">📄</div><div class="empty-state-text">Select a completed run to view the matched report</div></div></td></tr>';
        return;
    }
    const excelBtn = document.getElementById('downloadMatchedExcelBtn');
    badge.textContent = `Run #${report.run_id} • ${report.period_start||'—'} → ${report.period_end||'—'}`;
    printBtn.disabled = !report.matches || report.matches.length === 0;
    if (excelBtn) excelBtn.disabled = !report.matches || report.matches.length === 0;
    summary.innerHTML = `
        <div class="summary-item"><div class="summary-label">Matched Groups</div><div class="summary-value">${report.match_count||0}</div></div>
        <div class="summary-item"><div class="summary-label">Statement Entries</div><div class="summary-value">${report.statement_entry_count||0}</div></div>
        <div class="summary-item"><div class="summary-label">Ledger Entries</div><div class="summary-value">${report.bank_book_entry_count||0}</div></div>
        <div class="summary-item"><div class="summary-label">Matched Amount</div><div class="summary-value">₹${formatAmount(report.total_matched_amount||0)}</div></div>`;
    if (!report.matches || report.matches.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state"><div class="empty-state-icon">📄</div><div class="empty-state-text">No matched groups found</div></div></td></tr>';
        return;
    }
    tbody.innerHTML = report.matches.map(m => `<tr>
        <td><span class="badge badge-info">Pass ${m.pass_number}</span></td>
        <td>${formatMatchType(m.match_type)}</td>
        <td>${renderMatchedEntries(m.statement_entries, 'statement')}</td>
        <td>${renderMatchedEntries(m.bank_book_entries, 'book')}</td>
        <td><strong>₹${formatAmount(m.matched_amount)}</strong></td>
        <td>${escapeHtml(m.notes||'—')}</td></tr>`).join('');
}

function renderMatchedEntries(entries, kind) {
    if (!entries || entries.length === 0) return '<span style="color:var(--text-tertiary)">—</span>';
    return entries.map(e => {
        const txt = kind==='statement' ? (e.description||e.transaction_id||'—') : (e.narration||e.description||'—');
        const ref = e.references&&e.references.length ? `Ref: ${e.references.join(', ')}` : (e.transaction_id ? `Txn: ${e.transaction_id}` : '');
        return `<div style="margin-bottom:0.65rem"><div><strong>${escapeHtml(e.transaction_date||'—')}</strong> • ${e.direction==='IN'?'IN':'OUT'} • ₹${formatAmount(e.amount)}</div><div>${escapeHtml(truncate(txt,110))}</div>${ref?`<div style="font-size:0.8rem;color:var(--text-tertiary)">${escapeHtml(ref)}</div>`:''}</div>`;
    }).join('');
}

function downloadBrs(runId) {
    const a = document.createElement('a');
    a.href = `/api/reconciliation/run/${runId}/download`;
    a.download = `BRS_run_${runId}.xlsx`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
}

function downloadMatchedExcel(runId = null) {
    const target = runId || currentRunId || (currentMatchedReport && currentMatchedReport.run_id) || latestRunId;
    if (!target) { showAlert('No report available to download', 'danger'); return; }
    const a = document.createElement('a');
    a.href = `/api/reconciliation/run/${target}/matches/download`;
    a.download = `Matched_Report_Run_${target}.xlsx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

async function printMatchedReport(runId = null) {
    const target = runId || currentRunId || (currentMatchedReport && currentMatchedReport.run_id) || latestRunId;
    if (!target) { showAlert('No report available to print', 'danger'); return; }
    if (!currentMatchedReport || currentMatchedReport.run_id !== target)
        await loadMatchedReport(target, { switchToReports: false });
    if (!currentMatchedReport) return;
    const pw = window.open('', '_blank', 'noopener,noreferrer,width=1200,height=900');
    if (!pw) { showAlert('Popup blocked', 'danger'); return; }
    pw.document.open();
    pw.document.write(buildMatchedReportPrintHtml(currentMatchedReport));
    pw.document.close(); pw.focus();
    pw.onload = () => pw.print();
}

function buildMatchedReportPrintHtml(report) {
    const rows = (report.matches||[]).map(m => `<tr><td>Pass ${m.pass_number}</td><td>${escapeHtml(formatMatchType(m.match_type))}</td><td>${buildPrintableEntryList(m.statement_entries, 'statement')}</td><td>${buildPrintableEntryList(m.bank_book_entries, 'book')}</td><td>₹${formatAmount(m.matched_amount)}</td><td>${escapeHtml(m.notes||'—')}</td></tr>`).join('');
    return `<!DOCTYPE html><html lang="en-US"><head><meta charset="UTF-8"><title>Matched Report Run ${report.run_id}</title><style>body{font-family:"Segoe UI",Arial,sans-serif;margin:24px;color:#17212b}h1{margin:0 0 4px;font-size:24px}h2{margin:0 0 20px;font-size:16px;color:#4d5b6a;font-weight:500}.meta{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:18px}.meta div{padding:10px 14px;border:1px solid #d9e2ec;border-radius:8px;min-width:180px}.meta-label{font-size:12px;color:#61758a;text-transform:uppercase;letter-spacing:0.04em}.meta-value{font-size:18px;font-weight:700;margin-top:4px}table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border:1px solid #d9e2ec;padding:10px;vertical-align:top;font-size:12px}th{background:#eef4f8;text-align:left}.entry{margin-bottom:8px}</style></head><body><h1>Brainware University</h1><h2>Matched Report Run #${report.run_id} (${escapeHtml(report.period_start||'—')} to ${escapeHtml(report.period_end||'—')})</h2><div class="meta"><div><div class="meta-label">Matched Groups</div><div class="meta-value">${report.match_count||0}</div></div><div><div class="meta-label">Statement Entries</div><div class="meta-value">${report.statement_entry_count||0}</div></div><div><div class="meta-label">Ledger Entries</div><div class="meta-value">${report.bank_book_entry_count||0}</div></div><div><div class="meta-label">Matched Amount</div><div class="meta-value">₹${formatAmount(report.total_matched_amount||0)}</div></div></div><table><thead><tr><th>Pass</th><th>Match Type</th><th>Statement Entries</th><th>Bank Book Entries</th><th>Amount</th><th>Notes</th></tr></thead><tbody>${rows||'<tr><td colspan="6">No matched groups found.</td></tr>'}</tbody></table></body></html>`;
}

function buildPrintableEntryList(entries, kind) {
    if (!entries || entries.length === 0) return '—';
    return entries.map(e => {
        const txt = kind==='statement' ? (e.description||e.transaction_id||'—') : (e.narration||e.description||'—');
        const ref = e.references&&e.references.length ? `Ref: ${e.references.join(', ')}` : (e.transaction_id ? `Txn ID: ${e.transaction_id}` : '');
        return `<div class="entry"><div><strong>${escapeHtml(e.transaction_date||'—')}</strong> ${e.direction==='IN'?'IN':'OUT'} ₹${formatAmount(e.amount)}</div><div>${escapeHtml(txt)}</div>${ref?`<div>${escapeHtml(ref)}</div>`:''}</div>`;
    }).join('');
}

// ── Audit ──────────────────────────────────────────────────────
async function loadAuditLog() {
    try {
        const res = await authFetch('/api/audit?limit=50');
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        const logs = Array.isArray(data) ? data : [];
        const container = document.getElementById('auditLogContainer');
        if (logs.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📝</div><div class="empty-state-text">No audit entries yet</div></div>';
            return;
        }
        container.innerHTML = logs.map(log => `
            <div class="audit-item">
                <div class="audit-timestamp">${formatDateTime(log.timestamp)}</div>
                <div class="audit-action">${formatAction(log.action)}</div>
                ${log.entity_type ? `<div style="font-size:0.8rem;color:var(--text-tertiary)">${log.entity_type} #${log.entity_id||''}</div>` : ''}
            </div>
        `).join('');
    } catch (err) { console.error('Audit:', err); }
}

// ── Admin / User Management ────────────────────────────────────
async function loadUsers() {
    try {
        const res = await authFetch('/api/admin/users');
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        const users = Array.isArray(data) ? data : [];
        const tbody = document.getElementById('usersTableBody');
        if (users.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7"><div class="empty-state"><div class="empty-state-icon">👥</div><div class="empty-state-text">No users found</div></div></td></tr>';
            return;
        }
        tbody.innerHTML = users.map(u => `<tr>
            <td>${u.id}</td><td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.full_name)}</td>
            <td><span class="badge badge-info">${formatRole(u.role)}</span></td>
            <td>${escapeHtml(u.email||'—')}</td>
            <td>${u.is_active ? '<span class="badge badge-success">Active</span>' : '<span class="badge badge-danger">Disabled</span>'}</td>
            <td>
                ${u.is_active ? `<button class="btn btn-danger btn-sm" onclick="toggleUser(${u.id},false)" title="Deactivate">🚫</button>` : `<button class="btn btn-success btn-sm" onclick="toggleUser(${u.id},true)" title="Activate">✅</button>`}
            </td>
        </tr>`).join('');
    } catch (err) {
        showAlert(err.message, 'danger');
    }
}

async function doCreateUser() {
    const errEl = document.getElementById('newUserError');
    errEl.style.display = 'none';
    const body = {
        username: document.getElementById('newUserUsername').value.trim(),
        full_name: document.getElementById('newUserFullName').value.trim(),
        email: document.getElementById('newUserEmail').value.trim() || null,
        role: document.getElementById('newUserRole').value,
        password: document.getElementById('newUserPassword').value,
    };
    if (!body.username || !body.full_name || !body.password) {
        errEl.textContent = 'Username, full name, and password are required';
        errEl.style.display = 'block'; return;
    }
    try {
        const res = await authFetch('/api/admin/users', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        closeModal('createUserModal');
        showAlert('User created successfully', 'success');
        loadUsers();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
    }
}

async function toggleUser(userId, activate) {
    const action = activate ? 'activate' : 'deactivate';
    if (!confirm(`Are you sure you want to ${action} this user?`)) return;
    try {
        const res = await authFetch(`/api/admin/users/${userId}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: activate }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        showAlert(`User ${action}d`, 'success');
        loadUsers();
    } catch (err) { showAlert(err.message, 'danger'); }
}

async function clearDatabase() {
    if (!confirm('⚠️ This will DELETE all reconciliation data (runs, matches, exceptions, carry-forwards).\n\nUsers and audit log will be preserved.\n\nAre you absolutely sure?')) return;
    if (!confirm('FINAL CONFIRMATION: Type OK to proceed with clearing all data.')) return;
    try {
        const res = await authFetch('/api/admin/clear-database', { method: 'POST' });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        showAlert('Database cleared successfully', 'success');
        loadDashboard();
    } catch (err) { showAlert(err.message, 'danger'); }
}

// ── Change Password ────────────────────────────────────────────
async function doChangePassword() {
    const errEl = document.getElementById('cpError');
    errEl.style.display = 'none';
    const current = document.getElementById('cpCurrentPassword').value;
    const newPw = document.getElementById('cpNewPassword').value;
    const confirm_ = document.getElementById('cpConfirmPassword').value;
    if (!current || !newPw) { errEl.textContent = 'All fields are required'; errEl.style.display = 'block'; return; }
    if (newPw !== confirm_) { errEl.textContent = 'New passwords do not match'; errEl.style.display = 'block'; return; }
    if (newPw.length < 6) { errEl.textContent = 'Password must be at least 6 characters'; errEl.style.display = 'block'; return; }
    try {
        const res = await authFetch('/api/auth/change-password', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ current_password: current, new_password: newPw }),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        closeModal('changePasswordModal');
        showAlert('Password changed successfully', 'success');
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
    }
}

// ── Run Detail ─────────────────────────────────────────────────
async function viewRunDetail(runId) {
    try {
        const res = await authFetch(`/api/reconciliation/run/${runId}`);
        const run = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(run, 'Failed'));
        currentRunId = runId; currentExceptionRunId = runId;
        switchTab('reconciliation');
        document.getElementById('progressCard').style.display = 'none';
        showResults({
            run_id: run.id,
            total_bank_stmt: run.total_bank_stmt_entries || 0,
            total_bank_book: run.total_bank_book_entries || 0,
            total_matched: run.total_matched || 0,
            total_unmatched: run.total_unmatched || 0,
            auto_match_rate: run.total_bank_stmt_entries > 0
                ? ((run.total_matched / run.total_bank_stmt_entries) * 100).toFixed(1) : 0,
            carry_forward: run.total_pending || 0,
            pass_counts: { 1: run.pass1_matches||0, 2: run.pass2_matches||0, 3: run.pass3_matches||0, 4: run.pass4_matches||0 },
            totals: run.totals || null,
            section_summary: run.section_summary || null,
        });
    } catch (err) { console.error('View run:', err); }
}

// ── Modal helpers ──────────────────────────────────────────────
function openModal(id) { document.getElementById(id).classList.add('active'); }
function closeModal(id) { document.getElementById(id).classList.remove('active'); }

// ── Helpers ────────────────────────────────────────────────────
function showAlert(message, type) {
    const div = document.createElement('div');
    div.className = `alert alert-${type}`;
    div.textContent = message;
    Object.assign(div.style, { position:'fixed', top:'80px', right:'20px', zIndex:'9999', minWidth:'300px', boxShadow:'var(--shadow-lg)' });
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 4000);
}

function getStatusBadge(status) {
    const map = {
        running: '<span class="badge badge-info">⏳ Running</span>',
        completed: '<span class="badge badge-success">✓ Completed</span>',
        failed: '<span class="badge badge-danger">✗ Failed</span>',
        pending_review: '<span class="badge badge-warning">⏳ Pending Review</span>',
        approved: '<span class="badge badge-info">✓ Approved</span>',
        signed_off: '<span class="badge badge-success">✓ Signed Off</span>',
        open: '<span class="badge badge-warning">Open</span>',
        in_progress: '<span class="badge badge-info">In Progress</span>',
        escalated: '<span class="badge badge-danger">⬆ Escalated</span>',
        resolved: '<span class="badge badge-success">✓ Resolved</span>',
    };
    return map[status] || `<span class="badge badge-neutral">${status}</span>`;
}

function getExcTypeBadge(type) {
    const map = {
        unknown_dr: '<span class="badge badge-danger">Unknown DR</span>',
        unknown_cr: '<span class="badge badge-warning">Unknown CR</span>',
        gib_unmatched: '<span class="badge badge-info">GIB/Tax</span>',
        amount_mismatch: '<span class="badge badge-danger">Amount ≠</span>',
        stale_carry_forward: '<span class="badge badge-warning">Stale CF</span>',
        timing_difference: '<span class="badge badge-info">Timing Diff</span>',
    };
    return map[type] || '';
}

function formatSection(section) {
    const map = {
        add_cheque_issued: 'Cheque Issued Not Debited',
        add_bank_credit: 'Bank Credit Not in Book',
        less_cheque_deposit: 'Cheque Deposited Not Credited',
        less_cheque_deposited: 'Cheque Deposited Not Credited',
        less_bank_debit: 'Bank Debit Not in Book',
    };
    return map[section] || section;
}

function formatMatchType(type) {
    const map = {
        exact_ref: 'Exact Reference', exact_ref_multi_to_one: 'Batched Reference',
        one_to_many_ref: 'One to Many', narration_ref_group: 'Narration Group',
        text_exact: 'Text Exact', text_group: 'Text Group',
        rule_gib: 'GIB Rule', rule_bil: 'BIL Rule', rule_bil_group: 'BIL Group',
        rule_return: 'Return/Reversal', rule_statement_reversal: 'Statement Reversal',
        fd_booking: 'FD Booking', fd_maturity: 'FD Maturity', contra: 'Contra Transfer',
        amount_date_0: 'Amount + Date', amount_date_1: 'Amount ± 1 Day', amount_date_2: 'Amount ± 2 Days',
    };
    return map[type] || type;
}

function formatRole(role) {
    const map = {
        system_admin: 'System Admin', accounts_officer: 'Accounts Officer',
        accounts_manager: 'Accounts Manager', finance_controller: 'Finance Controller',
        internal_auditor: 'Internal Auditor',
    };
    return map[role] || role;
}

function formatAmount(val) { return parseFloat(val||0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function formatDateTime(dt) {
    if (!dt) return '—';
    try { const d = new Date(dt); return d.toLocaleDateString('en-IN', { day:'2-digit', month:'short', year:'numeric' }) + ' ' + d.toLocaleTimeString('en-IN', { hour:'2-digit', minute:'2-digit' }); }
    catch { return dt; }
}

function formatAction(action) {
    const map = {
        file_upload: '📄 File Uploaded', run_started: '▶ Run Started', run_completed: '✓ Run Completed',
        exception_created: '⚠ Exception Created', exception_resolved: '✓ Exception Resolved',
        exception_escalated: '⬆ Exception Escalated', exception_comment_added: '💬 Comment Added',
        approval_submitted: '📨 Submitted', approval_approved: '✓ Approved', approval_signoff: '✓ Signed Off',
        user_created: '👤 User Created', user_updated: '✏ User Updated', user_deactivated: '🚫 User Deactivated',
        database_cleared: '🗑 Database Cleared',
    };
    return map[action] || action;
}

function truncate(str, len) { if (!str) return ''; return str.length > len ? str.substring(0, len) + '...' : str; }
function escapeHtml(value) {
    return String(value||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Bank Accounts ──────────────────────────────────────────────
async function loadBankAccounts() {
    try {
        const res = await authFetch('/api/admin/bank-accounts');
        const data = await readResponseData(res);
        if (!res.ok) return;
        bankAccounts = Array.isArray(data) ? data : [];
        const select = document.getElementById('bankAccountSelect');
        if (select) {
            select.innerHTML = '<option value="">— Select Bank Account —</option>' +
                bankAccounts.map(a =>
                    `<option value="${a.id}">${escapeHtml(a.label)} (${escapeHtml(a.account_no)})</option>`
                ).join('');
            if (bankAccounts.length === 1) select.value = String(bankAccounts[0].id);
        }
        renderBankAccountSettings();
    } catch (err) { console.error('Bank accounts:', err); }
}

function renderBankAccountSettings() {
    const container = document.getElementById('bankAccountsList');
    const addRow = document.getElementById('addBankAccountRow');
    if (!container) return;

    const canManage = currentUser && (currentUser.role === 'system_admin' || currentUser.role === 'accounts_manager');
    if (addRow) addRow.style.display = canManage ? '' : 'none';

    if (bankAccounts.length === 0) {
        container.innerHTML = '<div class="setting-row"><div class="setting-info"><div class="setting-name">No bank accounts configured</div></div></div>';
        return;
    }
    container.innerHTML = bankAccounts.map(a => {
        const delBtn = canManage
            ? `<button class="btn btn-danger btn-sm" onclick="deleteBankAccount(${a.id}, '${escapeHtml(a.label)}')" title="Delete account"><span class="material-symbols-rounded btn-icon">delete</span></button>`
            : '';
        return `
        <div class="setting-row">
            <div class="setting-info">
                <div class="setting-name">${escapeHtml(a.bank_name)} — ${escapeHtml(a.branch || '')}</div>
                <div class="setting-desc">${escapeHtml(a.account_no)} — ${escapeHtml(a.account_type || 'Savings')}</div>
            </div>
            <div style="display:flex;align-items:center;gap:0.5rem">
                <span class="badge badge-${a.is_active ? 'success' : 'danger'}">${a.is_active ? 'Active' : 'Disabled'}</span>
                ${delBtn}
            </div>
        </div>`;
    }).join('');
}

async function doCreateBankAccount() {
    const errEl = document.getElementById('bankAccError');
    errEl.style.display = 'none';
    const body = {
        account_no: document.getElementById('newBankAccNo').value.trim(),
        bank_name: document.getElementById('newBankName').value.trim(),
        branch: document.getElementById('newBankBranch').value.trim(),
        account_type: document.getElementById('newBankAccType').value.trim() || 'Savings',
        label: document.getElementById('newBankLabel').value.trim(),
    };
    if (!body.account_no || !body.bank_name || !body.label) {
        errEl.textContent = 'Account number, bank name, and label are required';
        errEl.style.display = 'block'; return;
    }
    try {
        const res = await authFetch('/api/admin/bank-accounts', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed'));
        closeModal('addBankAccountModal');
        showAlert('Bank account created', 'success');
        loadBankAccounts();
    } catch (err) {
        errEl.textContent = err.message;
        errEl.style.display = 'block';
    }
}

async function deleteBankAccount(accountId, label) {
    if (!confirm(`Delete bank account "${label}"?\n\nThis cannot be undone. Accounts linked to reconciliation runs cannot be deleted.`)) return;
    try {
        const res = await authFetch(`/api/admin/bank-accounts/${accountId}`, { method: 'DELETE' });
        const data = await readResponseData(res);
        if (!res.ok) throw new Error(getResponseMessage(data, 'Failed to delete'));
        showAlert('Bank account deleted', 'success');
        loadBankAccounts();
    } catch (err) {
        showAlert(err.message, 'danger');
    }
}

// ── Init ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const now = new Date();
    const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
    const lastDay = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    document.getElementById('periodStart').value = firstDay.toISOString().split('T')[0];
    document.getElementById('periodEnd').value = lastDay.toISOString().split('T')[0];

    setupUpload('bsUploadArea', 'bsFileInput', 'bankStatement');
    setupUpload('bbUploadArea', 'bbFileInput', 'bankBook');
    setupUpload('prevBrsUploadArea', 'prevBrsFileInput', 'previousBrs');

    // Auto-login if token exists
    if (authToken) {
        showApp();
    }

    // Enter key on login form
    document.getElementById('loginPassword').addEventListener('keydown', e => {
        if (e.key === 'Enter') doLogin();
    });
    document.getElementById('loginUsername').addEventListener('keydown', e => {
        if (e.key === 'Enter') document.getElementById('loginPassword').focus();
    });
});
