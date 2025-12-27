/**
 * DoVi Convert Web Interface
 * Frontend JavaScript for real-time communication with the backend
 */

class DoViConvertApp {
    constructor() {
        console.log('DoViConvertApp constructor starting...');
        this.ws = null;
        this.isRunning = false;
        this.currentPath = '/media';
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.keepaliveInterval = null;
        this.logHistory = [];
        this.maxLogLines = 1000;
        
        // Pagination state
        this.allResults = null;
        this.filteredResults = [];
        this.currentResultsPage = 1;
        this.resultsPerPage = 20;
        
        // Selection and queue
        this.selectedFiles = new Set();
        this.conversionQueue = [];
        
        // Search and filter state
        this.searchTerm = '';
        this.filterProfile = 'all';
        this.sortBy = 'name';
        
        console.log('Initializing elements...');
        this.initElements();
        console.log('Initializing event listeners...');
        this.initEventListeners();
        console.log('Connecting WebSocket...');
        this.connectWebSocket();
        console.log('Loading settings...');
        this.loadSettings();
        console.log('Restoring state...');
        this.restoreState();
        console.log('Checking server status...');
        this.checkServerStatus();
        console.log('Loading stats...');
        this.loadStats();
        console.log('Loading cached results...');
        this.loadCachedResults();
        console.log('Constructor complete');
    }

    initElements() {
        const getEl = (id) => document.getElementById(id);
        
        // Status
        this.statusIndicator = getEl('statusIndicator');
        this.statusText = this.statusIndicator?.querySelector('.status-text');
        
        // Controls
        this.scanPathInput = getEl('scanPath');
        this.scanDepthInput = getEl('scanDepth');
        this.safeModeCheckbox = getEl('safeMode');
        this.includeSimpleCheckbox = getEl('includeSimple');
        this.autoCleanupCheckbox = getEl('autoCleanup');
        this.includeMoviesCheckbox = getEl('includeMovies');
        this.includeTvShowsCheckbox = getEl('includeTvShows');
        this.incrementalScanCheckbox = getEl('incrementalScan');
        
        // Jellyfin
        this.useJellyfinCheckbox = getEl('useJellyfin');
        this.jellyfinUrlInput = getEl('jellyfinUrl');
        this.jellyfinApiKeyInput = getEl('jellyfinApiKey');
        this.toggleApiKeyBtn = getEl('toggleApiKey');
        this.testJellyfinBtn = getEl('testJellyfin');
        this.jellyfinStatus = getEl('jellyfinStatus');
        
        // Schedule
        this.enableScheduleCheckbox = getEl('enableSchedule');
        this.scheduleTimeInput = getEl('scheduleTime');
        this.autoConvertCheckbox = getEl('autoConvert');
        
        // Buttons
        this.browseBtn = getEl('browseBtn');
        this.scanBtn = getEl('scanBtn');
        this.convertBtn = getEl('convertBtn');
        this.convertQueueBtn = getEl('convertQueueBtn');
        this.stopBtn = getEl('stopBtn');
        this.clearBtn = getEl('clearBtn');
        this.addToQueueBtn = getEl('addToQueueBtn');
        this.clearQueueBtn = getEl('clearQueueBtn');
        this.startQueueBtn = getEl('startQueueBtn');
        this.cleanBackupsBtn = getEl('cleanBackupsBtn');
        
        // Search and filter
        this.searchInput = getEl('searchResults');
        this.filterSelect = getEl('filterProfile');
        this.sortSelect = getEl('sortResults');
        this.selectAllCheckbox = getEl('selectAll');
        
        // Terminal
        this.terminal = getEl('terminal');
        this.terminalContent = getEl('terminalContent');
        
        // Modal
        this.modal = getEl('browserModal');
        this.currentPathDisplay = getEl('currentPath');
        this.directoryList = getEl('directoryList');
        this.modalClose = getEl('modalClose');
        this.modalCancel = getEl('modalCancel');
        this.modalSelect = getEl('modalSelect');
    }

    initEventListeners() {
        const addListener = (el, event, handler) => {
            if (el) el.addEventListener(event, handler);
        };
        
        // Browse button
        addListener(this.browseBtn, 'click', () => this.openBrowser());
        addListener(this.scanPathInput, 'click', () => this.openBrowser());
        
        // Action buttons
        addListener(this.scanBtn, 'click', () => this.startScan());
        addListener(this.convertBtn, 'click', () => this.convertSelected());
        addListener(this.convertQueueBtn, 'click', () => this.startQueue());
        addListener(this.stopBtn, 'click', () => this.stopProcess());
        addListener(this.clearBtn, 'click', () => this.clearTerminal());
        
        // Queue buttons
        addListener(this.addToQueueBtn, 'click', () => this.addSelectedToQueue());
        addListener(this.clearQueueBtn, 'click', () => this.clearQueue());
        addListener(this.startQueueBtn, 'click', () => this.startQueue());
        addListener(this.cleanBackupsBtn, 'click', () => this.cleanBackups());
        
        // Settings changes
        addListener(this.scanDepthInput, 'change', () => this.saveSettings());
        addListener(this.safeModeCheckbox, 'change', () => this.saveSettings());
        addListener(this.includeSimpleCheckbox, 'change', () => this.saveSettings());
        addListener(this.autoCleanupCheckbox, 'change', () => this.saveSettings());
        addListener(this.includeMoviesCheckbox, 'change', () => this.saveSettings());
        addListener(this.includeTvShowsCheckbox, 'change', () => this.saveSettings());
        addListener(this.enableScheduleCheckbox, 'change', () => this.saveSettings());
        addListener(this.scheduleTimeInput, 'change', () => this.saveSettings());
        addListener(this.autoConvertCheckbox, 'change', () => this.saveSettings());
        
        // Jellyfin settings
        addListener(this.useJellyfinCheckbox, 'change', () => {
            this.saveSettings();
            this.updateScanModeIndicator();
        });
        addListener(this.jellyfinUrlInput, 'change', () => this.saveSettings());
        addListener(this.jellyfinApiKeyInput, 'change', () => this.saveSettings());
        addListener(this.toggleApiKeyBtn, 'click', () => this.toggleApiKeyVisibility());
        addListener(this.testJellyfinBtn, 'click', () => this.testJellyfinConnection());
        
        // Search and filter
        addListener(this.searchInput, 'input', () => this.handleSearch());
        addListener(this.filterSelect, 'change', () => this.handleFilter());
        addListener(this.sortSelect, 'change', () => this.handleSort());
        addListener(this.selectAllCheckbox, 'change', () => this.toggleSelectAll());
        
        // Modal
        addListener(this.modalClose, 'click', () => this.closeModal());
        addListener(this.modalCancel, 'click', () => this.closeModal());
        addListener(this.modalSelect, 'click', () => this.selectDirectory());
        
        const modalBackdrop = this.modal?.querySelector('.modal-backdrop');
        addListener(modalBackdrop, 'click', () => this.closeModal());
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal?.classList.contains('active')) {
                this.closeModal();
            }
        });
        
        // Initialize tooltips
        this.initTooltips();
        
        // Tab switching (output panel)
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
        });
        
        // Control panel tab switching
        document.querySelectorAll('.control-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => this.switchControlTab(btn.dataset.controlTab));
        });
        
        // Pagination
        const prevBtn = document.getElementById('prevPage');
        const nextBtn = document.getElementById('nextPage');
        if (prevBtn) prevBtn.addEventListener('click', () => this.goToPage(this.currentResultsPage - 1));
        if (nextBtn) nextBtn.addEventListener('click', () => this.goToPage(this.currentResultsPage + 1));
        
        // Schedule day checkboxes
        document.querySelectorAll('input[name="scheduleDay"]').forEach(cb => {
            cb.addEventListener('change', () => this.saveSettings());
        });
    }
    
    // Search, Filter, Sort
    handleSearch() {
        this.searchTerm = this.searchInput?.value?.toLowerCase() || '';
        this.applyFilters();
    }
    
    handleFilter() {
        this.filterProfile = this.filterSelect?.value || 'all';
        this.applyFilters();
    }
    
    handleSort() {
        this.sortBy = this.sortSelect?.value || 'name';
        this.applyFilters();
    }
    
    applyFilters() {
        if (!this.allResults) return;
        
        const profile7 = this.allResults.profile7 || [];
        const profile8 = this.allResults.profile8 || [];
        
        // Combine and tag files
        let allFiles = [
            ...profile7.map(f => ({ ...f, type: 'convert', profileType: 'profile7' })),
            ...profile8.map(f => ({ ...f, type: 'compatible', profileType: 'profile8' }))
        ];
        
        // Filter by search term
        if (this.searchTerm) {
            allFiles = allFiles.filter(f => 
                f.name?.toLowerCase().includes(this.searchTerm) ||
                f.path?.toLowerCase().includes(this.searchTerm)
            );
        }
        
        // Filter by profile
        if (this.filterProfile === 'profile7') {
            allFiles = allFiles.filter(f => f.profileType === 'profile7');
        } else if (this.filterProfile === 'profile8') {
            allFiles = allFiles.filter(f => f.profileType === 'profile8');
        }
        
        // Sort
        allFiles.sort((a, b) => {
            switch (this.sortBy) {
                case 'type':
                    return a.type.localeCompare(b.type);
                case 'size':
                    return (b.size || 0) - (a.size || 0);
                default:
                    return (a.name || '').localeCompare(b.name || '');
            }
        });
        
        this.filteredResults = allFiles;
        this.currentResultsPage = 1;
        this.renderResultsPage();
    }
    
    // Selection
    toggleSelectAll() {
        const isChecked = this.selectAllCheckbox?.checked;
        
        if (isChecked) {
            // Select all visible (filtered) results
            this.filteredResults.forEach(f => {
                if (f.path) this.selectedFiles.add(f.path);
            });
        } else {
            this.selectedFiles.clear();
        }
        
        this.renderResultsPage();
        this.updateSelectionUI();
    }
    
    toggleFileSelection(path, checked) {
        if (checked) {
            this.selectedFiles.add(path);
        } else {
            this.selectedFiles.delete(path);
        }
        this.updateSelectionUI();
    }
    
    updateSelectionUI() {
        const count = this.selectedFiles.size;
        const countEl = document.getElementById('selectionCount');
        const selectedCountEl = document.getElementById('selectedCount');
        
        if (countEl) countEl.textContent = count;
        if (selectedCountEl) selectedCountEl.textContent = count;
        
        if (this.addToQueueBtn) this.addToQueueBtn.disabled = count === 0;
        if (this.convertBtn) this.convertBtn.disabled = count === 0;
        
        // Update select all checkbox state
        if (this.selectAllCheckbox && this.filteredResults.length > 0) {
            const allSelected = this.filteredResults.every(f => this.selectedFiles.has(f.path));
            const someSelected = this.filteredResults.some(f => this.selectedFiles.has(f.path));
            this.selectAllCheckbox.checked = allSelected;
            this.selectAllCheckbox.indeterminate = someSelected && !allSelected;
        }
    }
    
    // Queue Management
    addSelectedToQueue() {
        const profile7 = this.allResults?.profile7 || [];
        
        this.selectedFiles.forEach(path => {
            const file = profile7.find(f => f.path === path);
            if (file && !this.conversionQueue.some(q => q.path === path)) {
                this.conversionQueue.push(file);
            }
        });
        
        this.selectedFiles.clear();
        this.updateSelectionUI();
        this.renderQueue();
        this.renderResultsPage();
        this.switchControlTab('queue');
    }
    
    removeFromQueue(path) {
        this.conversionQueue = this.conversionQueue.filter(f => f.path !== path);
        this.renderQueue();
    }
    
    clearQueue() {
        this.conversionQueue = [];
        this.renderQueue();
    }
    
    renderQueue() {
        const list = document.getElementById('queueList');
        const countBadge = document.getElementById('queueCount');
        const countBadgeBtn = document.getElementById('queueCountBtn');
        const startBtn = this.startQueueBtn;
        
        if (countBadge) {
            countBadge.textContent = this.conversionQueue.length;
            countBadge.style.display = this.conversionQueue.length > 0 ? 'inline' : 'none';
        }
        
        // Update the action button queue count
        if (countBadgeBtn) {
            countBadgeBtn.textContent = this.conversionQueue.length;
        }
        
        // Enable/disable both queue buttons
        if (startBtn) {
            startBtn.disabled = this.conversionQueue.length === 0;
        }
        if (this.convertQueueBtn) {
            this.convertQueueBtn.disabled = this.conversionQueue.length === 0;
        }
        
        if (!list) return;
        
        if (this.conversionQueue.length === 0) {
            list.innerHTML = '<p class="empty-queue">No files in queue. Select files from Results to add them.</p>';
            return;
        }
        
        list.innerHTML = this.conversionQueue.map((file, idx) => `
            <div class="queue-item" data-path="${file.path}">
                <span class="queue-number">${idx + 1}</span>
                <span class="queue-name" title="${file.path}">${file.name}</span>
                <span class="remove-btn" onclick="app.removeFromQueue('${file.path.replace(/'/g, "\\'")}')">✕</span>
            </div>
        `).join('');
    }
    
    async startQueue() {
        if (this.conversionQueue.length === 0) return;
        
        const paths = this.conversionQueue.map(f => f.path);
        
        try {
            const response = await fetch('/api/convert', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: paths })
            });
            
            if (response.ok) {
                this.appendToTerminal(`📦 Starting conversion of ${paths.length} files...\n`, 'system');
                this.switchControlTab('actions');
                this.switchTab('output');
            }
        } catch (error) {
            this.appendToTerminal(`❌ Failed to start queue: ${error.message}\n`, 'error');
        }
    }
    
    async convertSelected() {
        if (this.selectedFiles.size === 0) return;
        
        // Add selected to queue first
        this.addSelectedToQueue();
        // Then start queue
        await this.startQueue();
    }
    
    // Stats
    async loadStats() {
        try {
            const response = await fetch('/api/stats');
            if (response.ok) {
                const stats = await response.json();
                this.updateStatsDisplay(stats);
            }
        } catch (error) {
            console.error('Failed to load stats:', error);
        }
    }
    
    async loadCachedResults() {
        try {
            const response = await fetch('/api/results');
            if (response.ok) {
                const data = await response.json();
                if (data.results) {
                    console.log('Restoring cached results:', data);
                    this.displayResults(data.results);
                    
                    // Update last scan time display if available
                    if (data.last_scan) {
                        const lastScanEl = document.getElementById('lastScanTime');
                        if (lastScanEl) {
                            const scanDate = new Date(data.last_scan);
                            lastScanEl.textContent = `Last scan: ${scanDate.toLocaleString()}`;
                        }
                    }
                }
            }
        } catch (error) {
            console.error('Failed to load cached results:', error);
        }
    }
    
    updateStatsDisplay(stats) {
        const setEl = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.textContent = value;
        };
        
        setEl('statProfile7', stats.profile7_count || 0);
        setEl('statProfile8', stats.profile8_count || 0);
        setEl('statHdr10', stats.hdr10_count || 0);
        setEl('statSdr', stats.sdr_count || 0);
        setEl('backupCount', stats.backup_count || 0);
        setEl('backupSize', this.formatSize(stats.backup_size || 0));
        
        // Update history list
        const historyList = document.getElementById('historyList');
        if (historyList && stats.history) {
            if (stats.history.length === 0) {
                historyList.innerHTML = '<p class="empty-history">No conversions yet.</p>';
            } else {
                historyList.innerHTML = stats.history.slice(0, 10).map(h => {
                    const statusIcon = h.status === 'success' ? '✅' : '❌';
                    const statusClass = h.status === 'success' ? 'success' : 'failed';
                    const logId = h.log_id || '';
                    return `
                    <div class="history-item ${statusClass}" data-log-id="${logId}" style="cursor: pointer;" title="Click to view log">
                        <span class="history-status">${statusIcon}</span>
                        <span class="history-file" title="${h.filename}">${h.filename}</span>
                        <span class="history-date">${new Date(h.date).toLocaleDateString()}</span>
                    </div>
                `}).join('');
                
                // Add click handlers for history items
                historyList.querySelectorAll('.history-item').forEach(item => {
                    item.addEventListener('click', () => {
                        const logId = item.dataset.logId;
                        if (logId) {
                            this.scrollToLog(logId);
                        }
                    });
                });
            }
        }
    }
    
    formatSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }
    
    async cleanBackups() {
        if (!confirm('Delete all backup files? This cannot be undone.')) return;
        
        try {
            const response = await fetch('/api/backups/clean', { method: 'POST' });
            if (response.ok) {
                const result = await response.json();
                this.appendToTerminal(`🧹 Cleaned ${result.deleted} backup files (${this.formatSize(result.freed)} freed)\n`, 'system');
                this.loadStats();
            }
        } catch (error) {
            this.appendToTerminal(`❌ Failed to clean backups: ${error.message}\n`, 'error');
        }
    }
    
    switchControlTab(tabName) {
        const tabs = document.querySelectorAll('.control-tab-btn');
        const contents = document.querySelectorAll('.control-tab-content');
        
        tabs.forEach(tab => {
            tab.classList.toggle('active', tab.dataset.controlTab === tabName);
        });
        
        contents.forEach(content => {
            content.classList.toggle('active', content.id === tabName + 'Tab');
        });
        
        // Refresh stats when switching to stats tab
        if (tabName === 'stats') {
            this.loadStats();
        }
    }
    
    updateScanModeIndicator() {
        const indicator = document.getElementById('scanModeValue');
        if (!indicator) return;
        
        const useJellyfin = this.useJellyfinCheckbox?.checked;
        if (useJellyfin) {
            indicator.textContent = 'Jellyfin (instant)';
            indicator.classList.add('jellyfin');
        } else {
            indicator.textContent = 'File System (mediainfo)';
            indicator.classList.remove('jellyfin');
        }
    }
    
    initTooltips() {
        try {
            const tooltips = document.querySelectorAll('.tooltip');
            tooltips.forEach(tooltip => {
                tooltip.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const text = tooltip.getAttribute('title');
                    this.showTooltipPopup(tooltip, text);
                });
            });
            
            document.addEventListener('click', () => {
                const existing = document.querySelector('.tooltip-popup');
                if (existing) existing.remove();
            });
        } catch (e) {
            console.error('Error initializing tooltips:', e);
        }
    }
    
    showTooltipPopup(element, text) {
        const existing = document.querySelector('.tooltip-popup');
        if (existing) existing.remove();
        
        const popup = document.createElement('div');
        popup.className = 'tooltip-popup';
        popup.textContent = text;
        document.body.appendChild(popup);
        
        const rect = element.getBoundingClientRect();
        popup.style.top = `${rect.bottom + 8}px`;
        popup.style.left = `${rect.left - 100}px`;
        
        setTimeout(() => popup.remove(), 3000);
    }
    
    connectWebSocket() {
        try {
            // Close existing connection first to prevent duplicates
            if (this.ws) {
                try {
                    this.ws.onclose = null; // Prevent reconnect loop
                    this.ws.close();
                } catch (e) {}
                this.ws = null;
            }
            
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
            
            if (this.terminalContent) {
                this.appendToTerminal(`🔌 Connecting to ${wsUrl}...\n`, 'system');
            }
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
                this.reconnectAttempts = 0;
                if (this.terminalContent) {
                    this.appendToTerminal('🔗 Connected to server\n', 'system');
                }
                this.startKeepalive();
        };
        
        this.ws.onmessage = (event) => {
                try {
            const data = JSON.parse(event.data);
                    if (data.type === 'ping' || data.type === 'keepalive') {
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send('ping');
                        }
                        return;
                    }
            this.handleMessage(data);
                } catch (e) {
                    if (event.data === 'pong') return;
                    console.error('Failed to parse WebSocket message:', e);
                }
            };
            
            this.ws.onclose = (event) => {
                this.stopKeepalive();
                if (this.terminalContent) {
                    this.appendToTerminal(`⚠️ Disconnected (code: ${event.code})\n`, 'error');
                }
                this.attemptReconnect();
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
                if (this.terminalContent) {
                    this.appendToTerminal('❌ WebSocket connection error\n', 'error');
                }
            };
        } catch (e) {
            console.error('Failed to create WebSocket:', e);
        }
    }
    
    startKeepalive() {
        this.stopKeepalive();
        this.keepaliveInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send('ping');
            }
        }, 20000);
    }
    
    stopKeepalive() {
        if (this.keepaliveInterval) {
            clearInterval(this.keepaliveInterval);
            this.keepaliveInterval = null;
        }
    }

    attemptReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
            this.appendToTerminal(`🔄 Reconnecting in ${delay/1000}s...\n`, 'system');
            setTimeout(() => this.connectWebSocket(), delay);
        } else {
            this.appendToTerminal('❌ Failed to reconnect. Please refresh the page.\n', 'error');
        }
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'status':
                this.updateStatus(data.running, data.action);
                if (data.settings) this.applySettings(data.settings);
                break;
            case 'output':
                this.appendToTerminal(data.data);
                break;
            case 'log_marker':
                this.addLogMarker(data.data.id, data.data.filename);
                break;
            case 'results':
                this.displayResults(data.data);
                this.loadStats();
                break;
            case 'progress':
                this.updateProgress(data.data);
                break;
            case 'conversion_complete':
                this.loadStats();
                this.loadCachedResults();
                // Show completion summary
                if (data.data && (data.data.successful !== undefined || data.data.failed !== undefined)) {
                    const success = data.data.successful || 0;
                    const failed = data.data.failed || 0;
                    if (failed > 0) {
                        this.showPopup(`Conversion complete: ${success} successful, ${failed} failed`, 'warning');
                    } else if (success > 0) {
                        this.showPopup(`Conversion complete: ${success} file(s) converted successfully!`, 'success');
                    }
                }
                break;
        }
    }
    
    updateProgress(progress) {
        const container = document.getElementById('progressContainer');
        const fill = document.getElementById('progressFill');
        const stats = document.getElementById('progressStats');
        const detail = document.getElementById('progressDetail');
        const label = document.getElementById('progressLabel');
        const percent = document.getElementById('progressPercent');
        
        if (!container) return;
        
        if (progress.status === 'scanning') {
            container.style.display = 'block';
            fill.classList.remove('indeterminate');
            fill.style.width = `${progress.percent}%`;
            stats.textContent = `${progress.current} / ${progress.total}`;
            if (percent) percent.textContent = `${progress.percent}%`;
            detail.textContent = progress.filename ? `📄 ${progress.filename}` : 'Processing...';
            label.textContent = 'Scanning files...';
            
            if (progress.eta) {
                detail.textContent += ` (ETA: ${progress.eta})`;
            }
        } else if (progress.status === 'converting') {
            container.style.display = 'block';
            fill.classList.remove('indeterminate');
            fill.style.width = `${progress.percent}%`;
            stats.textContent = `File ${progress.current} / ${progress.total}`;
            if (percent) percent.textContent = `${progress.percent}%`;
            
            // Show detailed conversion info
            let detailText = progress.filename ? `📄 ${progress.filename}` : 'Processing...';
            if (progress.step) {
                detailText += ` • ${progress.step}`;
            }
            if (progress.file_percent !== undefined && progress.file_percent > 0) {
                detailText += ` (${progress.file_percent}%)`;
            }
            detail.textContent = detailText;
            label.textContent = 'Converting...';
            
            if (progress.eta) {
                detail.textContent += ` • ETA: ${progress.eta}`;
            }
        } else if (progress.status === 'complete') {
            fill.classList.remove('indeterminate');
            fill.style.width = '100%';
            fill.style.background = 'var(--accent-success, #10b981)';
            if (percent) percent.textContent = '100%';
            label.textContent = 'Complete!';
            detail.textContent = '✓ Finished successfully';
            setTimeout(() => { 
                container.style.display = 'none';
                fill.classList.remove('indeterminate');
                fill.style.background = '';  // Reset color
            }, 3000);
        } else if (progress.status === 'failed') {
            fill.classList.remove('indeterminate');
            fill.style.width = '100%';
            fill.style.background = 'var(--accent-danger, #ef4444)';
            if (percent) percent.textContent = '✗';
            label.textContent = 'Failed!';
            detail.textContent = '❌ Conversion failed';
            setTimeout(() => { 
                container.style.display = 'none';
                fill.classList.remove('indeterminate');
                fill.style.background = '';  // Reset color
            }, 5000);
        } else if (progress.status === 'partial') {
            fill.classList.remove('indeterminate');
            fill.style.width = '100%';
            fill.style.background = 'var(--accent-warning, #f59e0b)';
            if (percent) percent.textContent = '!';
            label.textContent = 'Partial Success';
            detail.textContent = '⚠️ Some conversions failed';
            setTimeout(() => { 
                container.style.display = 'none';
                fill.classList.remove('indeterminate');
                fill.style.background = '';  // Reset color
            }, 5000);
        }
    }
    
    displayResults(results) {
        const summary = document.getElementById('resultsSummary');
        const countBadge = document.getElementById('resultCount');
        
        if (!summary) return;
        
        this.allResults = results;
        this.selectedFiles.clear();
        this.currentResultsPage = 1;
        
        const profile7 = results.profile7 || [];
        const profile8 = results.profile8 || [];
        
        if (countBadge) {
            countBadge.textContent = profile7.length > 0 ? profile7.length : '';
        }
        
        summary.innerHTML = `
            <div class="stats">
                <div class="stat profile7">
                    <span class="stat-value">${profile7.length}</span>
                    <span class="stat-label">Need Conversion<br>(Profile 7)</span>
                </div>
                <div class="stat profile8">
                    <span class="stat-value">${profile8.length}</span>
                    <span class="stat-label">Compatible<br>(Profile 8)</span>
                </div>
                <div class="stat">
                    <span class="stat-value">${results.hdr10_count || 0}</span>
                    <span class="stat-label">HDR10</span>
                </div>
                <div class="stat">
                    <span class="stat-value">${results.sdr_count || 0}</span>
                    <span class="stat-label">SDR</span>
                </div>
            </div>
        `;
        
        this.applyFilters();
        this.updateSelectionUI();
        this.switchTab('results');
    }
    
    renderResultsPage() {
        const list = document.getElementById('resultsList');
        const pagination = document.getElementById('pagination');
        const prevBtn = document.getElementById('prevPage');
        const nextBtn = document.getElementById('nextPage');
        const currentPageEl = document.getElementById('currentPage');
        const totalPagesEl = document.getElementById('totalPages');
        
        if (!list) return;
        
        const allFiles = this.filteredResults || [];
        const totalItems = allFiles.length;
        const totalPages = Math.ceil(totalItems / this.resultsPerPage) || 1;
        
        if (this.currentResultsPage > totalPages) this.currentResultsPage = totalPages;
        if (this.currentResultsPage < 1) this.currentResultsPage = 1;
        
        const startIndex = (this.currentResultsPage - 1) * this.resultsPerPage;
        const endIndex = Math.min(startIndex + this.resultsPerPage, totalItems);
        const pageItems = allFiles.slice(startIndex, endIndex);
        
        list.innerHTML = '';
        
        if (totalItems === 0) {
            list.innerHTML = '<p class="no-results">No files match your search/filter criteria.</p>';
            if (pagination) pagination.style.display = 'none';
            return;
        }
        
        pageItems.forEach(file => {
            const isSelected = this.selectedFiles.has(file.path);
            const item = document.createElement('div');
            item.className = `result-item${isSelected ? ' selected' : ''}`;
            const badgeClass = file.type === 'convert' ? 'convert' : 'compatible';
            const badgeText = file.type === 'convert' ? 'Needs Conversion' : 'Compatible';
            
            // Build media details string
            const details = [];
            if (file.resolution) details.push(file.resolution);
            if (file.codec) details.push(file.codec);
            if (file.size) details.push(this.formatSize(file.size));
            if (file.bitrate) details.push(file.bitrate);
            const detailsStr = details.length > 0 ? details.join(' • ') : '';
            
            // Get directory path (parent folder)
            const pathParts = (file.path || '').split('/');
            const parentDir = pathParts.slice(0, -1).join('/') || '/';
            
            item.innerHTML = `
                <input type="checkbox" class="result-checkbox" 
                    ${isSelected ? 'checked' : ''} 
                    ${file.type === 'compatible' ? 'disabled title="Only Profile 7 files can be converted"' : ''}
                    onchange="app.toggleFileSelection('${file.path?.replace(/'/g, "\\'")}', this.checked)">
                <div class="file-info">
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-hdr">
                        <span class="hdr-badge ${file.profileType || ''}">${file.hdr || file.profile || 'Dolby Vision'}</span>
                        ${detailsStr ? `<span class="media-details">${detailsStr}</span>` : ''}
                    </div>
                    <div class="file-path" title="${file.path}">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                        </svg>
                        ${parentDir}
                    </div>
                </div>
                <div class="file-action">
                    <span class="badge ${badgeClass}">${badgeText}</span>
            </div>
        `;
            list.appendChild(item);
        });
        
        if (pagination) {
            if (totalPages > 1) {
                pagination.style.display = 'flex';
                if (currentPageEl) currentPageEl.textContent = this.currentResultsPage;
                if (totalPagesEl) totalPagesEl.textContent = totalPages;
                if (prevBtn) prevBtn.disabled = this.currentResultsPage <= 1;
                if (nextBtn) nextBtn.disabled = this.currentResultsPage >= totalPages;
            } else {
                pagination.style.display = 'none';
            }
        }
    }
    
    goToPage(page) {
        const totalItems = this.filteredResults?.length || 0;
        const totalPages = Math.ceil(totalItems / this.resultsPerPage) || 1;
        
        if (page >= 1 && page <= totalPages) {
            this.currentResultsPage = page;
            this.renderResultsPage();
            const list = document.getElementById('resultsList');
            if (list) list.scrollTop = 0;
        }
    }
    
    switchTab(tabName) {
        document.querySelectorAll('.tab-btn').forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.toggle('active', content.id === tabName + 'Tab');
        });
    }

    updateStatus(running, action = '') {
        this.isRunning = running;
        
        if (running) {
            this.statusIndicator?.classList.add('running');
            if (this.statusText) this.statusText.textContent = action === 'scan' ? 'Scanning...' : 'Converting...';
            if (this.scanBtn) {
                this.scanBtn.disabled = true;
                this.scanBtn.classList.add('loading');
            }
            if (this.stopBtn) this.stopBtn.disabled = false;
        } else {
            this.statusIndicator?.classList.remove('running');
            if (this.statusText) this.statusText.textContent = 'Ready';
            if (this.scanBtn) {
                this.scanBtn.disabled = false;
                this.scanBtn.classList.remove('loading');
            }
            if (this.stopBtn) this.stopBtn.disabled = true;
        }
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();
            this.applySettings(settings);
        } catch (error) {
            console.error('Failed to load settings:', error);
        }
    }
    
    saveState() {
        try {
            sessionStorage.setItem('doviConvertState', JSON.stringify({
                logHistory: this.logHistory.slice(-this.maxLogLines),
                timestamp: Date.now()
            }));
        } catch (e) {
            console.warn('Failed to save state:', e);
        }
    }
    
    restoreState() {
        try {
            const saved = sessionStorage.getItem('doviConvertState');
            if (saved) {
                const state = JSON.parse(saved);
                if (Date.now() - state.timestamp < 3600000) {
                    this.logHistory = state.logHistory || [];
                    if (this.logHistory.length > 0 && this.terminalContent) {
                        this.terminalContent.innerHTML = '';
                        this.logHistory.forEach(entry => {
                            if (entry.type === 'marker' && entry.logId) {
                                this.addLogMarkerDirect(entry.logId, entry.filename);
                            } else {
                                this.appendToTerminalDirect(entry.text, entry.type);
                            }
                        });
                        this.appendToTerminal('── Session restored ──\n', 'system');
                    }
                }
            }
        } catch (e) {
            console.warn('Failed to restore state:', e);
        }
    }
    
    async checkServerStatus() {
        try {
            const response = await fetch('/api/status');
            const status = await response.json();
            if (status.is_running) {
                this.updateStatus(true, status.action || 'scan');
                this.appendToTerminal('🔄 Process still running on server...\n', 'system');
            }
        } catch (error) {
            console.error('Failed to check server status:', error);
        }
    }
    
    applySettings(settings) {
        if (settings.scan_path && this.scanPathInput) {
            this.scanPathInput.value = settings.scan_path;
            this.currentPath = settings.scan_path;
        }
        if (settings.scan_depth !== undefined && this.scanDepthInput) {
            this.scanDepthInput.value = settings.scan_depth;
        }
        if (this.safeModeCheckbox) this.safeModeCheckbox.checked = settings.safe_mode ?? false;
        if (this.includeSimpleCheckbox) this.includeSimpleCheckbox.checked = settings.include_simple_fel ?? false;
        if (this.autoCleanupCheckbox) this.autoCleanupCheckbox.checked = settings.auto_cleanup ?? false;
        if (this.includeMoviesCheckbox) this.includeMoviesCheckbox.checked = settings.include_movies ?? true;
        if (this.includeTvShowsCheckbox) this.includeTvShowsCheckbox.checked = settings.include_tv_shows ?? true;
        if (this.useJellyfinCheckbox) this.useJellyfinCheckbox.checked = settings.use_jellyfin ?? false;
        if (this.jellyfinUrlInput) this.jellyfinUrlInput.value = settings.jellyfin_url || '';
        if (this.jellyfinApiKeyInput) this.jellyfinApiKeyInput.value = settings.jellyfin_api_key || '';
        if (this.enableScheduleCheckbox) this.enableScheduleCheckbox.checked = settings.schedule_enabled ?? false;
        if (this.scheduleTimeInput) this.scheduleTimeInput.value = settings.schedule_time || '02:00';
        if (this.autoConvertCheckbox) this.autoConvertCheckbox.checked = settings.auto_convert ?? false;
        
        // Schedule days
        const days = settings.schedule_days || [6];
        document.querySelectorAll('input[name="scheduleDay"]').forEach(cb => {
            cb.checked = days.includes(parseInt(cb.value));
        });
        
        this.updateScanModeIndicator();
    }
    
    async saveSettings() {
        const scheduleDays = [];
        document.querySelectorAll('input[name="scheduleDay"]:checked').forEach(cb => {
            scheduleDays.push(parseInt(cb.value));
        });
        
        const settings = {
            scan_path: this.scanPathInput?.value,
            scan_depth: parseInt(this.scanDepthInput?.value, 10) || 5,
            safe_mode: this.safeModeCheckbox?.checked ?? false,
            include_simple_fel: this.includeSimpleCheckbox?.checked ?? false,
            auto_cleanup: this.autoCleanupCheckbox?.checked ?? false,
            include_movies: this.includeMoviesCheckbox?.checked ?? true,
            include_tv_shows: this.includeTvShowsCheckbox?.checked ?? true,
            use_jellyfin: this.useJellyfinCheckbox?.checked ?? false,
            jellyfin_url: this.jellyfinUrlInput?.value || '',
            jellyfin_api_key: this.jellyfinApiKeyInput?.value || '',
            schedule_enabled: this.enableScheduleCheckbox?.checked ?? false,
            schedule_time: this.scheduleTimeInput?.value || '02:00',
            schedule_days: scheduleDays,
            auto_convert: this.autoConvertCheckbox?.checked ?? false
        };
        
        try {
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
        } catch (error) {
            console.error('Failed to save settings:', error);
        }
    }
    
    toggleApiKeyVisibility() {
        if (this.jellyfinApiKeyInput) {
            this.jellyfinApiKeyInput.type = this.jellyfinApiKeyInput.type === 'password' ? 'text' : 'password';
        }
    }
    
    async testJellyfinConnection() {
        if (!this.jellyfinStatus) return;
        
        const url = this.jellyfinUrlInput?.value;
        const apiKey = this.jellyfinApiKeyInput?.value;
        
        if (!url || !apiKey) {
            this.jellyfinStatus.className = 'jellyfin-status error';
            this.jellyfinStatus.textContent = 'Please enter URL and API key';
            return;
        }
        
        await this.saveSettings();
        
        this.jellyfinStatus.className = 'jellyfin-status testing';
        this.jellyfinStatus.textContent = 'Testing connection...';
        
        try {
            const response = await fetch('/api/jellyfin/test', { method: 'POST' });
            const data = await response.json();
            
            if (response.ok && data.success) {
                this.jellyfinStatus.className = 'jellyfin-status success';
                this.jellyfinStatus.textContent = `✓ Connected to ${data.server_name} (v${data.version})`;
            } else {
                this.jellyfinStatus.className = 'jellyfin-status error';
                this.jellyfinStatus.textContent = `✗ ${data.detail || 'Connection failed'}`;
            }
        } catch (error) {
            this.jellyfinStatus.className = 'jellyfin-status error';
            this.jellyfinStatus.textContent = `✗ ${error.message}`;
        }
    }
    
    async startScan() {
        this.clearTerminal();
        
        // Show immediate visual feedback
        if (this.scanBtn) {
            this.scanBtn.classList.add('loading');
            this.scanBtn.disabled = true;
        }
        
        // Show progress bar immediately with "initializing" state
        const progressContainer = document.getElementById('progressContainer');
        const progressFill = document.getElementById('progressFill');
        const progressLabel = document.getElementById('progressLabel');
        const progressPercent = document.getElementById('progressPercent');
        const progressDetail = document.getElementById('progressDetail');
        const progressStats = document.getElementById('progressStats');
        
        if (progressContainer) {
            progressContainer.style.display = 'block';
            if (progressFill) {
                progressFill.style.width = '30%';
                progressFill.classList.add('indeterminate');
            }
            if (progressLabel) progressLabel.textContent = 'Initializing scan...';
            if (progressPercent) progressPercent.textContent = '';
            if (progressDetail) progressDetail.textContent = 'Connecting to server...';
            if (progressStats) progressStats.textContent = '';
        }
        
        // Switch to Log tab to show output
        this.switchTab('output');
        
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.appendToTerminal('🔌 Reconnecting WebSocket...\n', 'system');
            this.connectWebSocket();
            await new Promise(resolve => setTimeout(resolve, 1000));
        }
        
        const incremental = this.incrementalScanCheckbox?.checked ?? true;
        this.appendToTerminal(`📡 Starting ${incremental ? 'incremental' : 'full'} scan...\n`, 'system');
        
        try {
            const response = await fetch('/api/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ incremental })
            });
            const data = await response.json();
            
            if (!response.ok) {
                this.appendToTerminal(`❌ Server error: ${data.detail || response.statusText}\n`, 'error');
                // Reset button and hide progress on error
                if (this.scanBtn) {
                    this.scanBtn.classList.remove('loading');
                    this.scanBtn.disabled = false;
                }
                if (progressContainer) progressContainer.style.display = 'none';
                if (progressFill) progressFill.classList.remove('indeterminate');
            }
        } catch (error) {
            this.appendToTerminal(`❌ Failed to start scan: ${error.message}\n`, 'error');
            // Reset button and hide progress on error
            if (this.scanBtn) {
                this.scanBtn.classList.remove('loading');
                this.scanBtn.disabled = false;
            }
            if (progressContainer) progressContainer.style.display = 'none';
            if (progressFill) progressFill.classList.remove('indeterminate');
        }
    }
    
    async stopProcess() {
        try {
            await fetch('/api/stop', { method: 'POST' });
        } catch (error) {
            this.appendToTerminal(`❌ Failed to stop: ${error.message}\n`, 'error');
        }
    }

    appendToTerminal(text, type = 'normal') {
        this.logHistory.push({ text, type, time: Date.now() });
        if (this.logHistory.length > this.maxLogLines) this.logHistory.shift();
        this.saveState();
        this.appendToTerminalDirect(text, type);
    }
    
    appendToTerminalDirect(text, type = 'normal') {
        if (!this.terminalContent) return;
        
        const welcomeMsg = this.terminalContent.querySelector('.welcome-msg');
        if (welcomeMsg) this.terminalContent.innerHTML = '';
        
        const span = document.createElement('span');
        span.textContent = text;
        
        if (type === 'error') span.style.color = 'var(--accent-danger)';
        else if (type === 'system') span.style.color = 'var(--text-muted)';
        
        if (text.includes('✅') || text.includes('SUCCESS')) span.style.color = 'var(--accent-secondary)';
        else if (text.includes('❌') || text.includes('ERROR')) span.style.color = 'var(--accent-danger)';
        else if (text.includes('⚠️') || text.includes('WARNING')) span.style.color = 'var(--accent-warning)';
        else if (text.includes('🔍') || text.includes('🎬')) span.style.color = 'var(--accent-primary)';
        
        this.terminalContent.appendChild(span);
        this.terminalContent.scrollTop = this.terminalContent.scrollHeight;
    }

    addLogMarker(logId, filename) {
        if (!this.terminalContent) return;
        
        this.addLogMarkerDirect(logId, filename);
        
        // Also store in log history for restoration
        this.logHistory.push({ text: '', type: 'marker', logId, filename, time: Date.now() });
        this.saveState();
    }
    
    addLogMarkerDirect(logId, filename) {
        if (!this.terminalContent) return;
        
        // Create an invisible anchor element for scrolling
        const marker = document.createElement('span');
        marker.id = `log-${logId}`;
        marker.className = 'log-marker';
        marker.dataset.filename = filename;
        this.terminalContent.appendChild(marker);
    }
    
    scrollToLog(logId) {
        // Switch to log tab first
        this.switchTab('output');
        
        // Find the marker
        const marker = document.getElementById(`log-${logId}`);
        if (marker) {
            // Highlight the marker briefly
            marker.scrollIntoView({ behavior: 'smooth', block: 'start' });
            
            // Find the next sibling elements and highlight them
            let el = marker.nextElementSibling;
            const toHighlight = [];
            let count = 0;
            while (el && count < 10) {
                toHighlight.push(el);
                el = el.nextElementSibling;
                count++;
            }
            
            // Add highlight animation
            toHighlight.forEach(span => {
                span.classList.add('log-highlight');
                setTimeout(() => span.classList.remove('log-highlight'), 2000);
            });
        } else {
            this.showPopup('Log entry not found - it may have been cleared', 'warning');
        }
    }
    
    clearTerminal() {
        if (this.terminalContent) this.terminalContent.innerHTML = '';
        this.logHistory = [];
        this.saveState();
    }

    async openBrowser() {
        this.modal?.classList.add('active');
        await this.loadDirectory(this.currentPath);
    }

    closeModal() {
        this.modal?.classList.remove('active');
    }

    async loadDirectory(path) {
        try {
            const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            
            this.currentPath = data.current;
            if (this.currentPathDisplay) this.currentPathDisplay.textContent = data.current;
            if (this.directoryList) this.directoryList.innerHTML = '';
            
            if (data.parent) {
                this.directoryList?.appendChild(this.createDirectoryItem('..', data.parent, true));
            }
            
            for (const dir of data.directories) {
                this.directoryList?.appendChild(this.createDirectoryItem(dir.name, dir.path));
            }
            
            if (data.directories.length === 0 && !data.parent && this.directoryList) {
                this.directoryList.innerHTML = '<p style="color: var(--text-muted); padding: 1rem;">No subdirectories found</p>';
            }
        } catch (error) {
            console.error('Failed to browse directory:', error);
            if (this.directoryList) {
                this.directoryList.innerHTML = '<p style="color: var(--accent-danger); padding: 1rem;">Failed to load directory</p>';
            }
        }
    }

    createDirectoryItem(name, path, isParent = false) {
        const item = document.createElement('div');
        item.className = `directory-item${isParent ? ' parent' : ''}`;
        item.innerHTML = `
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
            </svg>
            <span>${name}</span>
        `;
        item.addEventListener('click', () => this.loadDirectory(path));
        return item;
    }
    
    selectDirectory() {
        if (this.scanPathInput) this.scanPathInput.value = this.currentPath;
        this.saveSettings();
        this.closeModal();
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    try {
        window.app = new DoViConvertApp();
    } catch (error) {
        console.error('Failed to initialize app:', error);
        document.body.innerHTML = `<pre style="color: red; padding: 20px;">Error: ${error.message}\n\n${error.stack}</pre>`;
    }
});


