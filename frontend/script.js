/**
 * 
 * Flow:
 *  1. TA enters a CRN -> verified against VALID_CRN -> saved preferences and
 *     formula config are loaded from the API in parallel.
 *  2. TA uploads a Canvas gradebook CSV -> the backend categorises columns and
 *     returns them; renderCategorySelections() builds the checkbox UI.
 *  3. TA reviews/adjusts column selections and dropdown overrides for
 *     Attendance and Final Exam, then clicks "Save & Calculate Grades".
 *  4. Selections are persisted to the API and the normalization endpoint is
 *     called; displayNormalizedResults() renders the output table.
 *
 * savedPreferenceState intentionally survives between CSV uploads (i.e. it is
 * NOT cleared in resetToUploadNewFile) so TAs can process multiple lab-section
 * CSVs without reselecting the same columns each time.
 *
 * Two entry modes:
 *  CRN mode: any valid 5–6 digit CRN; preferences, formula config, and
 *              categories are loaded from and saved to the API.
 *  Guest mode: no CRN required; full normalization works end-to-end but
 *              nothing is persisted to the database. Formula overrides are
 *              sent inline with each normalize request instead.
 */

import { API_URL, CATEGORY_LABELS } from "./js/constants.js";
import { capitalize, showMessage } from "./js/helpers.js";

// ─── Theme toggle ────────────────────────────────────────────────────────────
const themeToggle = document.getElementById('themeToggle');
const themeIcon = document.getElementById('themeIcon');

const savedTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', savedTheme);
themeIcon.src = savedTheme === 'dark' ? './assets/darkmode.png' : './assets/lightmode.png';
themeIcon.alt = savedTheme === 'dark' ? 'Dark mode' : 'Light mode';

themeToggle.addEventListener('click', () => {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    themeIcon.src = newTheme === 'dark' ? './assets/darkmode.png' : './assets/lightmode.png';
    themeIcon.alt = newTheme === 'dark' ? 'Dark mode' : 'Light mode';
});

 
// ─── CRN section — state and DOM refs ────────────────────────────────────────
const crnInput     = document.getElementById('crnInput');
const submitCrnBtn = document.getElementById('submitCrn');
const crnMessage   = document.getElementById('crnMessage');
const uploadSection= document.getElementById('uploadSection');

let currentUserId = 'guest';                // overwritten with the CRN on verify
let isGuestMode = false;                    // true when "Continue as Guest" was chosen
let userCategories = null;                  // custom or default category config
let latestCategorizedColumns = {};          // most recent upload's column-to-category map
let savedPreferenceState = null;            // persists across CSV re-uploads (CRN mode only)
let formulaConfig = null;                   // active formula weights for this CRN

// Maps each formula input's element ID to the backend config key and display label.
// Used by save/populate/reset handlers to avoid duplicating field metadata.
const FORMULA_FIELDS = [
    { id: 'inputLabWeight',           key: 'lab_weight',              label: 'Lab weight' },
    { id: 'inputDdWeight',            key: 'dd_weight',               label: 'Debug Dungeon weight' },
    { id: 'inputLabScale',            key: 'lab_scale',               label: 'Lab score multiplier' },
    { id: 'inputLabTotalPoints',      key: 'lab_total_points',        label: 'Lab total points' },
    { id: 'inputAttendanceMult',      key: 'attendance_multiplier',   label: 'Attendance multiplier' },
    { id: 'inputAttendanceTotalPoints', key: 'attendance_total_points', label: 'Attendance total points' },
    { id: 'inputLabDenomFallback',    key: 'lab_denominator_fallback', label: 'Lab max points (fallback)' },
    { id: 'inputDdDenomFallback',     key: 'dd_denominator_fallback', label: 'Debug Dungeon max points (fallback)' },
];
 
// Restrict CRN input to digits only, preventing accidental letter entry.
crnInput.addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/[^0-9]/g, '');
});
 
submitCrnBtn.addEventListener('click', async () => {
    const entered = crnInput.value.trim();
    if (entered.length < 5 || entered.length > 6) {
        showMessage(crnMessage, 'Please enter a valid 5-6 digit CRN', 'error');
        return;
    }

    isGuestMode   = false;
    currentUserId = entered;
    await Promise.all([loadSavedPreferences(), loadFormulaConfig()]);

    showMessage(crnMessage, 'CRN verified successfully!', 'success');
    setTimeout(() => {
        uploadSection.classList.add('active');
        crnInput.disabled     = true;
        submitCrnBtn.disabled = true;
        document.getElementById('continueAsGuest').disabled = true;
    }, 500);
});

document.getElementById('continueAsGuest').addEventListener('click', () => {
    isGuestMode   = true;
    currentUserId = 'guest';

    // Show the guest banner inside the upload section.
    const banner = document.getElementById('guestBanner');
    if (banner) banner.style.display = 'flex';

    uploadSection.classList.add('active');
    crnInput.disabled     = true;
    submitCrnBtn.disabled = true;
    document.getElementById('continueAsGuest').disabled = true;
});
 
crnInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') submitCrnBtn.click();
});
 
 
// ─── API data loaders ─────────────────────────────────────────────────────────

async function loadUserCategories() {
    try {
        const response = await fetch(`${API_URL}/categories/${currentUserId}`);
        const data = await response.json();
        
        if (response.ok) {
            userCategories = data.categories;
            renderCategoryEditor(data.categories);
        }
    } catch (err) {
        console.error('Failed to load categories:', err);
        const defaultResponse = await fetch(`${API_URL}/categories/defaults`);
        userCategories = await defaultResponse.json();
        renderCategoryEditor(userCategories);
    }
}

async function loadFormulaConfig() {
    // Guest sessions have no saved config 
    if (isGuestMode) return;
    try {
        const response = await fetch(`${API_URL}/config/${currentUserId}`);
        if (!response.ok) return;
        const data = await response.json();
        formulaConfig = data.config;
        populateFormulaFields(formulaConfig);
    } catch (err) {
        console.error('Failed to load formula config:', err);
    }
}

function populateFormulaFields(config) {
    if (!config) return;
    FORMULA_FIELDS.forEach(({ id, key }) => {
        const input = document.getElementById(id);
        if (input && config[key] !== undefined) {
            input.value = config[key];
        }
    });
}

async function loadSavedPreferences() {
    savedPreferenceState = null;
    // Guest sessions have no stored preferences 
    if (isGuestMode) return;
    try {
        const response = await fetch(`${API_URL}/preferences/${currentUserId}`);
        if (!response.ok) return;
        const data = await response.json();
        const prefs = data.preferences;
        if (Array.isArray(prefs)) {
            savedPreferenceState = {
                selected_columns: prefs,
                selected_attendance_column: null,
                selected_final_exam_column: null,
            };
            return;
        }
        if (prefs && typeof prefs === 'object') {
            savedPreferenceState = {
                selected_columns: Array.isArray(prefs.selected_columns) ? prefs.selected_columns : [],
                selected_attendance_column: prefs.selected_attendance_column || null,
                selected_final_exam_column: prefs.selected_final_exam_column || null,
            };
        }
    } catch (err) {
        console.error('Failed to load saved preferences:', err);
    }
}
 
// ─── Category editor ──────────────────────────────────────────────────────────

document.getElementById('toggleCategories').addEventListener('click', () => {
    const editor = document.getElementById('categoryEditor');
    const toggleText = document.getElementById('toggleText');
    
    if (editor.style.display === 'none') {
        editor.style.display = 'block';
        toggleText.textContent = 'Hide';
        if (!userCategories) loadUserCategories();
    } else {
        editor.style.display = 'none';
        toggleText.textContent = 'Show';
    }
});
 
function renderCategoryEditor(categories) {
    const categoryList = document.getElementById('categoryList');
    categoryList.innerHTML = '';
    
    Object.entries(categories).forEach(([catName, catConfig]) => {
        const keywords = catConfig.keywords || [];
        
        const categoryItem = document.createElement('div');
        categoryItem.className = 'category-edit-item';
        categoryItem.innerHTML = `
            <div class="category-edit-header">
                <input type="text" class="category-name-input" value="${catName}" data-original="${catName}">
                <button class="btn-remove" data-category="${catName}">×</button>
            </div>
            <input type="text" class="category-keywords-input" 
                   value="${keywords.join(', ')}" 
                   placeholder="Enter keywords separated by commas"
                   data-category="${catName}">
            <p class="hint">Keywords are case-insensitive and matched anywhere in column names</p>
        `;
        categoryList.appendChild(categoryItem);
    });
    
    document.querySelectorAll('.btn-remove').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.target.closest('.category-edit-item').remove();
        });
    });
}
 
document.getElementById('addCategory').addEventListener('click', () => {
    const categoryList = document.getElementById('categoryList');
    
    const categoryItem = document.createElement('div');
    categoryItem.className = 'category-edit-item';
    categoryItem.innerHTML = `
        <div class="category-edit-header">
            <input type="text" class="category-name-input" value="" placeholder="Category name" data-original="">
            <button class="btn-remove">×</button>
        </div>
        <input type="text" class="category-keywords-input" 
               value="" 
               placeholder="Enter keywords separated by commas"
               data-category="">
        <p class="hint">Keywords are case-insensitive and matched anywhere in column names</p>
    `;
    categoryList.appendChild(categoryItem);
    
    categoryItem.querySelector('.btn-remove').addEventListener('click', (e) => {
        e.target.closest('.category-edit-item').remove();
    });
});
 
document.getElementById('saveCategories').addEventListener('click', async () => {
    if (isGuestMode) {
        showMessage(
            document.getElementById('categoryMessage'),
            'Guest mode (verify a CRN to save custom categories).',
            'error'
        );
        return;
    }

    const categoryItems = document.querySelectorAll('.category-edit-item');
    const categories = {};
    
    categoryItems.forEach(item => {
        const nameInput = item.querySelector('.category-name-input');
        const keywordsInput = item.querySelector('.category-keywords-input');
        
        const name = nameInput.value.trim();
        const keywords = keywordsInput.value
            .split(',')
            .map(k => k.trim())
            .filter(k => k.length > 0);
        
        if (name && keywords.length > 0) {
            categories[name] = { keywords };
        }
    });
    
    if (Object.keys(categories).length === 0) {
        showMessage(document.getElementById('categoryMessage'), 
                   'Please add at least one category with keywords', 'error');
        return;
    }
    
    const saveBtn = document.getElementById('saveCategories');
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving...';
    
    try {
        const response = await fetch(`${API_URL}/categories`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_id: currentUserId,
                categories: categories
            })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            userCategories = categories;
            showMessage(document.getElementById('categoryMessage'), 
                       '✓ Categories saved successfully!', 'success');
        } else {
            throw new Error(data.error || 'Save failed');
        }
    } catch (err) {
        showMessage(document.getElementById('categoryMessage'), 
                   `✗ ${err.message}`, 'error');
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save Categories';
    }
});
 
 
// ─── Formula config editor ────────────────────────────────────────────────────

document.getElementById('toggleFormula').addEventListener('click', () => {
    const editor = document.getElementById('formulaEditor');
    const text   = document.getElementById('toggleFormulaText');
    if (editor.style.display === 'none') {
        editor.style.display = 'block';
        text.textContent = 'Hide';
    } else {
        editor.style.display = 'none';
        text.textContent = 'Show';
    }
});

document.getElementById('saveFormula').addEventListener('click', async () => {
    if (isGuestMode) {
        showMessage(
            document.getElementById('formulaMessage'),
            'Guest mode (verify a CRN to save formula settings.)',
            'error'
        );
        return;
    }

    const config = {};
    const errors = [];

    FORMULA_FIELDS.forEach(({ id, key, label }) => {
        const raw = document.getElementById(id)?.value.trim();
        if (!raw) return; // blank = keep using the server-side default

        // Reject anything that isn't a finite positive number.
        const num = Number(raw);
        if (!Number.isFinite(num) || raw === '') {
            errors.push(`"${label}" must be a number`);
        } else if (num <= 0) {
            errors.push(`"${label}" must be greater than zero`);
        } else {
            config[key] = num;
        }
    });

    if (errors.length > 0) {
        showMessage(
            document.getElementById('formulaMessage'),
            errors.join('<br>'),
            'error'
        );
        return;
    }

    const saveBtn = document.getElementById('saveFormula');
    saveBtn.disabled    = true;
    saveBtn.textContent = 'Saving...';

    try {
        const response = await fetch(`${API_URL}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: currentUserId, config }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Save failed');
        formulaConfig = data.config;
        showMessage(document.getElementById('formulaMessage'), '✓ Formula saved successfully!', 'success');
    } catch (err) {
        showMessage(document.getElementById('formulaMessage'), `✗ ${err.message}`, 'error');
    } finally {
        saveBtn.disabled    = false;
        saveBtn.textContent = 'Save Formula';
    }
});

document.getElementById('resetFormula').addEventListener('click', async () => {
    const resetBtn = document.getElementById('resetFormula');

    // In guest mode there is nothing to clear on the server so just wipe the inputs locally.
    if (isGuestMode) {
        formulaConfig = null;
        FORMULA_FIELDS.forEach(({ id }) => {
            const input = document.getElementById(id);
            if (input) input.value = '';
        });
        showMessage(document.getElementById('formulaMessage'), '✓ Formula reset to defaults.', 'success');
        return;
    }

    resetBtn.disabled    = true;
    resetBtn.textContent = 'Resetting...';

    try {
        const response = await fetch(`${API_URL}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: currentUserId, config: {} }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Reset failed');

        formulaConfig = {};
        FORMULA_FIELDS.forEach(({ id }) => {
            const input = document.getElementById(id);
            if (input) input.value = '';
        });
        showMessage(document.getElementById('formulaMessage'), '✓ Formula reset to defaults.', 'success');
    } catch (err) {
        showMessage(document.getElementById('formulaMessage'), `✗ ${err.message}`, 'error');
    } finally {
        resetBtn.disabled    = false;
        resetBtn.textContent = 'Reset to Defaults';
    }
});

// ─── CSV file input and upload ────────────────────────────────────────────────

const csvFileInput   = document.getElementById('csvFile');
const fileLabel      = document.getElementById('fileLabel');
const fileName       = document.getElementById('fileName');
const processFileBtn = document.getElementById('processFile');
const uploadMessage  = document.getElementById('uploadMessage');
 
csvFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (file.name.endsWith('.csv')) {
        fileName.textContent = file.name;
        fileLabel.classList.add('has-file');
        processFileBtn.disabled = false;
    } else {
        fileName.textContent = 'Please select a CSV file';
        fileLabel.classList.remove('has-file');
        processFileBtn.disabled = true;
        showMessage(uploadMessage, 'Please upload a valid CSV file', 'error');
    }
});
 
fileLabel.addEventListener('dragover',  (e) => { e.preventDefault(); fileLabel.style.borderColor = 'var(--accent)'; });
fileLabel.addEventListener('dragleave', ()  => { fileLabel.style.borderColor = 'var(--border)'; });
fileLabel.addEventListener('drop', (e) => {
    e.preventDefault();
    fileLabel.style.borderColor = 'var(--border)';
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.csv')) {
        csvFileInput.files = e.dataTransfer.files;
        fileName.textContent = file.name;
        fileLabel.classList.add('has-file');
        processFileBtn.disabled = false;
    }
});

function resetToUploadNewFile() {
    document.getElementById('selectionsPanel')?.remove();
    document.getElementById('resultsPanel')?.remove();
    document.getElementById('debugPanel')?.remove();

    latestCategorizedColumns = {};
    fileLabel.style.borderColor = 'var(--border)';
    fileLabel.classList.remove('has-file');
    fileName.textContent = 'Choose CSV file or drag here';
    csvFileInput.value = '';
    processFileBtn.disabled = true;
    processFileBtn.textContent = 'Process & Normalize Grades';
    uploadMessage.innerHTML = '';

    uploadSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
 
processFileBtn.addEventListener('click', async () => {
    const file = csvFileInput.files[0];
    if (!file) return;
 
    processFileBtn.disabled    = true;
    processFileBtn.textContent = 'Uploading';
    showMessage(uploadMessage, 'Parsing CSV', 'success');
 
    try {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('user_id', currentUserId);
 
        const response = await fetch(`${API_URL}/upload`, {
            method: 'POST',
            body: formData,
        });
 
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Upload failed');
 
        showMessage(uploadMessage, `✓ Found ${data.row_count} student records`, 'success');
        renderCategorySelections(data.categories);
 
    } catch (err) {
        showMessage(uploadMessage, `✗ ${err.message}`, 'error');
    } finally {
        processFileBtn.disabled    = false;
        processFileBtn.textContent = 'Process & Normalize Grades';
    }
});
 
 
// ─── Column-selection panel ───────────────────────────────────────────────────

function renderCategorySelections(categories) {
    document.getElementById('selectionsPanel')?.remove();
    latestCategorizedColumns = categories || {};
    const allColumns = Object.values(categories || {}).flat();
    const attendanceCandidates = allColumns.filter(col => col.toLowerCase().includes('attendance'));
    const finalExamCandidates = allColumns.filter(col => col.toLowerCase().includes('final exam part2'));
    const attendanceDefault = attendanceCandidates.find(col => col.toLowerCase().trim() === 'attendance total') || attendanceCandidates[0] || '';
    const finalExamDefault = finalExamCandidates.find(col => col.toLowerCase().includes('coding assessment')) || finalExamCandidates[0] || '';

    const hasSavedColumnPrefs =
        savedPreferenceState &&
        Array.isArray(savedPreferenceState.selected_columns) &&
        savedPreferenceState.selected_columns.length > 0;
    const sessionCollapsed = sessionStorage.getItem('selectionsPanelCollapsed') === '1';
    const startCollapsed = sessionCollapsed || hasSavedColumnPrefs;

    // Columns controlled exclusively by the dropdowns — checkboxes are disabled for these.
    const dropdownControlledCols = new Set([...attendanceCandidates, ...finalExamCandidates]);

    // Only these categories feed into the grade calculation.
    const CALCULATION_CATEGORIES = new Set(['labs', 'debug_dungeon', 'participation']);

    const renderColCheckbox = (col, category) => {
        if (dropdownControlledCols.has(col)) {
            return `
                <label class="checkbox-label" style="opacity:0.5;cursor:default;" title="This column is selected via the dropdown above">
                    <input type="checkbox" name="preference" value="${col}" data-category="${category}" disabled>
                    <span>${col} <em style="font-size:0.8em;color:var(--text-secondary);">(via dropdown)</em></span>
                </label>
            `;
        }
        if (!CALCULATION_CATEGORIES.has(category)) {
            return `
                <label class="checkbox-label" style="opacity:0.45;cursor:default;">
                    <input type="checkbox" name="preference" value="${col}" data-category="${category}" disabled>
                    <span>${col}</span>
                </label>
            `;
        }
        return `
            <label class="checkbox-label">
                <input type="checkbox" name="preference" value="${col}" data-category="${category}" checked>
                <span>${col}</span>
            </label>
        `;
    };

    const renderCategoryGroup = (category, cols) => {
        const isCalc = CALCULATION_CATEGORIES.has(category);
        const hasActiveCheckboxes = isCalc && cols.some(col => !dropdownControlledCols.has(col));
        return `
            <div class="category-group"${!isCalc ? ' style="opacity:0.5;"' : ''}>
                <div class="category-header">
                    <p class="category-title">${CATEGORY_LABELS[category] || capitalize(category)}</p>
                    <label class="checkbox-label category-checkbox" style="${!hasActiveCheckboxes ? 'opacity:0.5;pointer-events:none;' : ''}">
                        <input type="checkbox" class="category-select" data-category="${category}"${hasActiveCheckboxes ? ' checked' : ' disabled'}>
                        <span style="font-size:0.85rem;color:var(--text-secondary);">Select all</span>
                    </label>
                </div>
                ${!isCalc ? '<p class="hint" style="margin-top:0;margin-bottom:0.5rem;">Not included in grade calculation.</p>' : ''}
                <div class="category-items">
                    ${cols.map(col => renderColCheckbox(col, category)).join('')}
                </div>
            </div>
        `;
    };

    const categoriesHTML = Object.entries(categories).map(([category, cols]) => renderCategoryGroup(category, cols)).join('');

    const panel = document.createElement('div');
    panel.id = 'selectionsPanel';
    panel.innerHTML = `
        <div class="decorative-line"></div>
        <div class="selections-panel-toolbar">
            <div class="selections-toolbar-row">
                <p class="form-label selections-toolbar-title">Select Required Fields</p>
                <button type="button" class="btn-link" id="toggleSelectionsCollapse" aria-expanded="${startCollapsed ? 'false' : 'true'}">
                    <span id="selectionsCollapseLabel">${startCollapsed ? 'Show' : 'Hide'}</span>
                    <span id="selectionsCollapseChevron" class="selections-collapse-chevron" aria-hidden="true">${startCollapsed ? '▼' : '▲'}</span>
                </button>
            </div>
            <p class="selections-toolbar-hint">Expand to change which columns are included. Your saved preferences still apply when collapsed.</p>
        </div>
        <div id="selectionsCollapsible" class="selections-collapsible${startCollapsed ? ' selections-collapsible--collapsed' : ''}">
            <div class="selection-header">
                <p class="form-label" style="margin-bottom:0;">Column checkboxes</p>
                <label class="checkbox-label master-checkbox">
                    <input type="checkbox" id="selectAllMaster" checked>
                    <span style="font-weight:600;">Select All</span>
                </label>
            </div>
            <p style="color:var(--text-secondary);font-size:0.95rem;margin-bottom:1.2rem;">
                Choose which columns to include in the grade calculation.
                ${!isGuestMode ? 'Previously saved preferences are applied automatically.' : ''}
            </p>
            <div class="column-choice-grid">
                <div class="column-choice">
                    <label class="form-label" for="attendanceColumnSelect">Attendance column (choose one)</label>
                    ${attendanceCandidates.length > 0
                        ? `<select id="attendanceColumnSelect" class="form-select">
                            ${attendanceCandidates.map(col => `<option value="${col}" ${col === attendanceDefault ? 'selected' : ''}>${col}</option>`).join('')}
                           </select>`
                        : `<p class="hint" style="margin-top:0.4rem;">No column headers found matching attendance category keywords. Attendance will be recorded as 0.</p>`
                    }
                </div>
                <div class="column-choice">
                    <label class="form-label" for="finalExamColumnSelect">Final Exam column (choose one)</label>
                    ${finalExamCandidates.length > 0
                        ? `<select id="finalExamColumnSelect" class="form-select">
                            ${finalExamCandidates.map(col => `<option value="${col}" ${col === finalExamDefault ? 'selected' : ''}>${col}</option>`).join('')}
                           </select>`
                        : `<p class="hint" style="margin-top:0.4rem;">No column headers found matching final exam category keywords. Final Exam 2 will be recorded as 0.</p>`
                    }
                </div>
            </div>
            <div id="calculationScope" class="scope-summary"></div>
            ${categoriesHTML}
        </div>
        <div class="action-row">
            <button type="button" class="btn btn-primary" id="savePreferences" style="margin-top:1.5rem;">
                ${isGuestMode ? 'Calculate Grades' : 'Save & Calculate Grades'}
            </button>
            <button type="button" class="btn btn-secondary" id="debugNormalize" style="margin-top:1.5rem;">
                Debug Normalize
            </button>
        </div>
        ${isGuestMode ? '<p class="hint" style="margin-top:0.5rem;">Guest mode — results will not be saved.</p>' : ''}
        <div id="prefMessage"></div>
    `;
 
    document.querySelector('.card').appendChild(panel);

    const collapsible = document.getElementById('selectionsCollapsible');
    const toggleCollapseBtn = document.getElementById('toggleSelectionsCollapse');
    const collapseLabel = document.getElementById('selectionsCollapseLabel');
    const collapseChevron = document.getElementById('selectionsCollapseChevron');
    const setSelectionsCollapsed = (collapsed) => {
        if (!collapsible || !toggleCollapseBtn) return;
        collapsible.classList.toggle('selections-collapsible--collapsed', collapsed);
        toggleCollapseBtn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        if (collapseLabel) collapseLabel.textContent = collapsed ? 'Show' : 'Hide';
        if (collapseChevron) collapseChevron.textContent = collapsed ? '▼' : '▲';
        sessionStorage.setItem('selectionsPanelCollapsed', collapsed ? '1' : '0');
    };
    toggleCollapseBtn?.addEventListener('click', () => {
        const isCollapsed = collapsible?.classList.contains('selections-collapsible--collapsed');
        setSelectionsCollapsed(!isCollapsed);
    });

    setupCheckboxHandlers();
    
    const saveBtn = document.getElementById('savePreferences');
    saveBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        savePreferences(e);
    });

    const debugBtn = document.getElementById('debugNormalize');
    debugBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        runDebugNormalization();
    });

    document.getElementById('attendanceColumnSelect')?.addEventListener('change', updateCalculationScopeSummary);
    document.getElementById('finalExamColumnSelect')?.addEventListener('change', updateCalculationScopeSummary);
    applySavedPreferencesToSelections();
    updateCalculationScopeSummary();
}

// ─── Checkbox state helpers ───────────────────────────────────────────────────

function applySavedPreferencesToSelections() {
    if (!savedPreferenceState) return;

    const selectedColumnsSet = new Set(savedPreferenceState.selected_columns || []);
    if (selectedColumnsSet.size > 0) {
        document.querySelectorAll('input[name="preference"]').forEach(cb => {
            cb.checked = selectedColumnsSet.has(cb.value);
        });

        document.querySelectorAll('.category-select').forEach(categoryBox => {
            updateCategoryCheckbox(categoryBox.dataset.category);
        });
        updateMasterCheckbox();
    }

    const attendanceSelect = document.getElementById('attendanceColumnSelect');
    if (
        attendanceSelect &&
        savedPreferenceState.selected_attendance_column &&
        [...attendanceSelect.options].some(opt => opt.value === savedPreferenceState.selected_attendance_column)
    ) {
        attendanceSelect.value = savedPreferenceState.selected_attendance_column;
    }

    const finalExamSelect = document.getElementById('finalExamColumnSelect');
    if (
        finalExamSelect &&
        savedPreferenceState.selected_final_exam_column &&
        [...finalExamSelect.options].some(opt => opt.value === savedPreferenceState.selected_final_exam_column)
    ) {
        finalExamSelect.value = savedPreferenceState.selected_final_exam_column;
    }
}
 
function setupCheckboxHandlers() {
    const masterCheckbox = document.getElementById('selectAllMaster');
    const categoryCheckboxes = document.querySelectorAll('.category-select');
    const itemCheckboxes = document.querySelectorAll('input[name="preference"]');
    
    masterCheckbox.addEventListener('change', (e) => {
        const checked = e.target.checked;
        itemCheckboxes.forEach(cb => { if (!cb.disabled) cb.checked = checked; });
        categoryCheckboxes.forEach(cb => { if (!cb.disabled) cb.checked = checked; });
    });
    
    categoryCheckboxes.forEach(categoryBox => {
        categoryBox.addEventListener('change', (e) => {
            const category = e.target.dataset.category;
            const checked = e.target.checked;
            const itemsInCategory = document.querySelectorAll(`input[name="preference"][data-category="${category}"]`);
            itemsInCategory.forEach(cb => { if (!cb.disabled) cb.checked = checked; });
            updateMasterCheckbox();
        });
    });
    
    itemCheckboxes.forEach(itemBox => {
        itemBox.addEventListener('change', () => {
            updateCategoryCheckbox(itemBox.dataset.category);
            updateMasterCheckbox();
            updateCalculationScopeSummary();
        });
    });
}
 
function updateCategoryCheckbox(category) {
    const categoryBox = document.querySelector(`.category-select[data-category="${category}"]`);
    const itemsInCategory = [...document.querySelectorAll(`input[name="preference"][data-category="${category}"]`)].filter(cb => !cb.disabled);
    const allChecked = itemsInCategory.every(cb => cb.checked);
    const anyChecked = itemsInCategory.some(cb => cb.checked);
    
    if (categoryBox) {
        categoryBox.checked = allChecked;
        categoryBox.indeterminate = anyChecked && !allChecked;
    }
}
 
function updateMasterCheckbox() {
    const masterCheckbox = document.getElementById('selectAllMaster');
    const allItems = [...document.querySelectorAll('input[name="preference"]')].filter(cb => !cb.disabled);
    const allChecked = allItems.every(cb => cb.checked);
    const anyChecked = allItems.some(cb => cb.checked);
    
    if (masterCheckbox) {
        masterCheckbox.checked = allChecked;
        masterCheckbox.indeterminate = anyChecked && !allChecked;
    }
}
 
// Returns the dropdown-selected attendance and final exam column names.
// Returns empty strings when the relevant dropdown is absent (no candidates found).
function getSelectedSingleColumns() {
    const selectedAttendance = document.getElementById('attendanceColumnSelect')?.value || '';
    const selectedFinalExam = document.getElementById('finalExamColumnSelect')?.value || '';
    return { selectedAttendance, selectedFinalExam };
}

function updateCalculationScopeSummary() {
    const scopeEl = document.getElementById('calculationScope');
    if (!scopeEl) return;
    const selectedFields = getSelectedFields();
    const labCount = selectedFields.filter(f => f.category === 'labs').length;
    const ddCount = selectedFields.filter(f => f.category === 'debug_dungeon' || f.category === 'participation').length;
    const { selectedAttendance, selectedFinalExam } = getSelectedSingleColumns();
    scopeEl.innerHTML = `
        <strong>Calculation scope:</strong>
        Labs selected: ${labCount},
        Debug Dungeon selected: ${ddCount},
        Attendance column: ${selectedAttendance || 'None found'},
        Final Exam column: ${selectedFinalExam || 'None found'}
    `;
}

 
// ─── Normalization and preferences persistence ────────────────────────────────

/**
 * Read the formula weight inputs and return a config object for inline use.
 * Only fields with valid, positive finite numbers are included. Blank fields
 * are omitted so the backend falls back to its own defaults for those keys.
 * Used by guest sessions to pass formula overrides directly in the request body.
 */
function getFormulaConfigFromUI() {
    const config = {};
    FORMULA_FIELDS.forEach(({ id, key }) => {
        const raw = document.getElementById(id)?.value.trim();
        if (!raw) return;
        const num = Number(raw);
        if (Number.isFinite(num) && num > 0) config[key] = num;
    });
    return Object.keys(config).length > 0 ? config : null;
}

async function persistUserPreferencesToServer(checkedColumns, selectedAttendance, selectedFinalExam) {
    // Guest sessions skip persistence entirely — nothing is stored to the DB.
    if (isGuestMode) return;

    const prefResponse = await fetch(`${API_URL}/save-preferences`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            user_id: String(currentUserId).trim(),
            preferences: checkedColumns,
            selected_attendance_column: selectedAttendance || null,
            selected_final_exam_column: selectedFinalExam || null,
        }),
    });
    if (!prefResponse.ok) {
        const data = await prefResponse.json();
        throw new Error(data.error || 'Failed to save preferences');
    }
    savedPreferenceState = {
        selected_columns: checkedColumns,
        selected_attendance_column: selectedAttendance || null,
        selected_final_exam_column: selectedFinalExam || null,
    };
}

async function savePreferences(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    
    const checked = getSelectedFields();
 
    if (checked.length === 0) {
        showMessage(document.getElementById('prefMessage'), 'Select at least one field', 'error');
        return;
    }
 
    const saveBtn = document.getElementById('savePreferences');
    saveBtn.disabled    = true;
    saveBtn.textContent = 'Saving & Calculating';
 
    try {
        const { selectedAttendance, selectedFinalExam } = getSelectedSingleColumns();
        await persistUserPreferencesToServer(
            checked.map(c => c.column),
            selectedAttendance,
            selectedFinalExam
        );
        await runNormalization(checked);
 
    } catch (err) {
        showMessage(document.getElementById('prefMessage'), `✗ ${err.message}`, 'error');
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save & Calculate Grades';
    }
}

function getSelectedFields() {
    return [...document.querySelectorAll('input[name="preference"]:checked')]
        .filter(cb => !cb.disabled)
        .map(cb => ({ column: cb.value, category: cb.dataset.category }));
}
 
async function runNormalization(selectedFields) {
    try {
        const { selectedAttendance, selectedFinalExam } = getSelectedSingleColumns();

        // Guests cannot store formula config, so send any UI overrides inline.
        const body = {
            user_id: currentUserId,
            selected_fields: selectedFields,
            selected_attendance_column: selectedAttendance || null,
            selected_final_exam_column: selectedFinalExam || null,
        };
        if (isGuestMode) {
            const uiConfig = getFormulaConfigFromUI();
            if (uiConfig) body.formula_config = uiConfig;
        }

        const response = await fetch(`${API_URL}/normalize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
 
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Normalization failed');
 
        displayNormalizedResults(data);
        
    } catch (err) {
        showMessage(document.getElementById('prefMessage'), `✗ ${err.message}`, 'error');
        document.getElementById('savePreferences').disabled = false;
        document.getElementById('savePreferences').textContent = 'Save & Calculate Grades';
    }
}

async function runDebugNormalization() {
    const selectedFields = getSelectedFields();
    if (selectedFields.length === 0) {
        showMessage(document.getElementById('prefMessage'), 'Select at least one field', 'error');
        return;
    }

    const debugBtn = document.getElementById('debugNormalize');
    const saveBtn = document.getElementById('savePreferences');
    debugBtn.disabled = true;
    saveBtn.disabled = true;
    debugBtn.textContent = 'Running Debug';

    try {
        const { selectedAttendance, selectedFinalExam } = getSelectedSingleColumns();
        await persistUserPreferencesToServer(
            selectedFields.map((f) => f.column),
            selectedAttendance,
            selectedFinalExam
        );

        // Guests send formula overrides inline (same pattern as runNormalization).
        const body = {
            user_id: currentUserId,
            selected_fields: selectedFields,
            selected_attendance_column: selectedAttendance || null,
            selected_final_exam_column: selectedFinalExam || null,
        };
        if (isGuestMode) {
            const uiConfig = getFormulaConfigFromUI();
            if (uiConfig) body.formula_config = uiConfig;
        }

        const response = await fetch(`${API_URL}/normalize/debug`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Debug normalization failed');

        displayNormalizedResults(data);
        displayDebugResults(data.debug);
        showMessage(document.getElementById('prefMessage'), '✓ Debug normalization complete', 'success');
    } catch (err) {
        showMessage(document.getElementById('prefMessage'), `✗ ${err.message}`, 'error');
    } finally {
        debugBtn.disabled = false;
        saveBtn.disabled = false;
        debugBtn.textContent = 'Debug Normalize';
    }
}
 
// ─── Results display ──────────────────────────────────────────────────────────

function displayNormalizedResults(data) {
    document.getElementById('resultsPanel')?.remove();
    document.getElementById('debugPanel')?.remove();
    
    const panel = document.createElement('div');
    panel.id = 'resultsPanel';
    panel.innerHTML = `
        <div class="decorative-line"></div>
        <h2 style="color:var(--accent);margin-bottom:1rem;">Normalized Grade Results</h2>
        <div style="overflow-x:auto;margin-bottom:1.5rem;">
            <table id="resultsTable" style="width:100%;border-collapse:collapse;"></table>
        </div>
        <div class="action-row results-actions">
            <button type="button" class="btn btn-primary" id="downloadCSV">
                Download Results (CSV)
            </button>
            <button type="button" class="btn btn-secondary" id="uploadAnotherFile">
                Upload another CSV
            </button>
        </div>
    `;
    
    document.querySelector('.card').appendChild(panel);

    const table = document.getElementById('resultsTable');
    const headers = data.columns;
    const rows = data.data;

    let tableHTML = '<thead><tr>';
    headers.forEach(h => {
        tableHTML += `<th style="padding:0.75rem;background:var(--bg-secondary);border:1px solid var(--border);text-align:left;">${h}</th>`;
    });
    tableHTML += '</tr></thead><tbody>';
    
    rows.forEach(row => {
        tableHTML += '<tr>';
        headers.forEach(h => {
            const value = row[h] !== undefined && row[h] !== null ? row[h] : '';
            tableHTML += `<td style="padding:0.75rem;border:1px solid var(--border);">${value}</td>`;
        });
        tableHTML += '</tr>';
    });
    tableHTML += '</tbody>';
    
    table.innerHTML = tableHTML;
    document.getElementById('downloadCSV').addEventListener('click', () => {
        downloadCSV(data.columns, data.data, `normalized_grades_${currentUserId}.csv`);
    });
    document.getElementById('uploadAnotherFile').addEventListener('click', () => {
        resetToUploadNewFile();
    });
    
    showMessage(document.getElementById('prefMessage'), 
               'Grades calculated successfully!', 'success');
    
    document.getElementById('savePreferences').disabled = false;
    document.getElementById('savePreferences').textContent = 'Save & Calculate Grades';
}

function displayDebugResults(debug) {
    document.getElementById('debugPanel')?.remove();
    if (!debug) return;

    const panel = document.createElement('div');
    panel.id = 'debugPanel';
    const perStudentRows = (debug.per_student || []).map((row) => `
        <tr>
            <td>${row.student ?? ''}</td>
            <td>${row.lab_raw_sum ?? ''}</td>
            <td>${row.lab_component ?? ''}</td>
            <td>${row.dd_raw_sum ?? ''}</td>
            <td>${row.dd_component ?? ''}</td>
            <td>${row.lab_before_rounding ?? ''}</td>
            <td>${row.attendance_source ?? ''}</td>
            <td>${row.attendance_before_rounding ?? ''}</td>
            <td>${row.final_exam_2 ?? ''}</td>
        </tr>
    `).join('');

    panel.innerHTML = `
        <div class="decorative-line"></div>
        <h3 class="debug-title">Normalization Debug Details</h3>
        <p class="debug-text"><strong>Detected Student:</strong> ${debug.detected_columns?.student ?? 'N/A'}</p>
        <p class="debug-text"><strong>Detected Attendance:</strong> ${debug.detected_columns?.attendance ?? 'N/A'}</p>
        <p class="debug-text"><strong>Detected Final Exam 2:</strong> ${debug.detected_columns?.final_exam_2 ?? 'N/A'}</p>
        <p class="debug-text"><strong>Lab Denominator:</strong> ${debug.denominators?.lab ?? 'N/A'} (${debug.denominators?.lab_source ?? 'N/A'})</p>
        <p class="debug-text"><strong>DD Denominator:</strong> ${debug.denominators?.debug_dungeon ?? 'N/A'} (${debug.denominators?.debug_dungeon_source ?? 'N/A'})</p>
        <p class="debug-text"><strong>Rows:</strong> input ${debug.row_counts?.input_rows ?? 'N/A'}, students normalized ${debug.row_counts?.student_rows_normalized ?? debug.row_counts?.clean_rows ?? 'N/A'}, points-possible ${debug.row_counts?.points_possible_rows_found ?? 'N/A'}</p>
        <div class="debug-list"><strong>Lab Columns:</strong> ${(debug.detected_columns?.labs || []).join(', ') || 'None'}</div>
        <div class="debug-list"><strong>Debug Dungeon Columns:</strong> ${(debug.detected_columns?.debug_dungeon || []).join(', ') || 'None'}</div>
        <div class="debug-table-wrap">
            <table class="debug-table">
                <thead>
                    <tr>
                        <th>Student</th>
                        <th>Lab Raw Sum</th>
                        <th>Lab Component</th>
                        <th>DD Raw Sum</th>
                        <th>DD Component</th>
                        <th>Lab Pre-Round</th>
                        <th>Attendance Source</th>
                        <th>Attendance Pre-Round</th>
                        <th>Final Exam 2</th>
                    </tr>
                </thead>
                <tbody>${perStudentRows}</tbody>
            </table>
        </div>
    `;

    document.querySelector('.card').appendChild(panel);
}
 
// ─── CSV export ───────────────────────────────────────────────────────────────

function downloadCSV(columns, data, filename) {
    let csv = columns.join(',') + '\n';
    data.forEach(row => {
        const values = columns.map(col => {
            const val = row[col] !== undefined && row[col] !== null ? row[col] : '';
            return `"${val}"`;
        });
        csv += values.join(',') + '\n';
    });
    
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
}
 
 