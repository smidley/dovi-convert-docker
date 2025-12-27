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
        this.currentResultsPage = 1;
        this.resultsPerPage = 20;
        
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
        console.log('Constructor complete');
    }

    initElements() {
        // Helper to get element safely
        const getEl = (id) => {
            const el = document.getElementById(id);
            if (!el) console.warn(`Element not found: ${id}`);
            return el;
        };
        
        // Status
        this.statusIndicator = getEl('statusIndicator');
        this.statusText = this.statusIndicator?.querySelector('.status-text');
        
        // Controls
        this.scanPathInput = getEl('scanPath');
        this.scanDepthInput = getEl('scanDepth');
        this.safeModeCheckbox = getEl('safeMode');
        this.includeSimpleCheckbox = getEl('includeSimple');
        this.autoCleanupCheckbox = getEl('autoCleanup');
        
        // Jellyfin
        this.useJellyfinCheckbox = getEl('useJellyfin');
        this.jellyfinUrlInput = getEl('jellyfinUrl');
        this.jellyfinApiKeyInput = getEl('jellyfinApiKey');
        this.toggleApiKeyBtn = getEl('toggleApiKey');
        this.testJellyfinBtn = getEl('testJellyfin');
        this.jellyfinStatus = getEl('jellyfinStatus');
        
        // Buttons
        this.browseBtn = getEl('browseBtn');
        this.scanBtn = getEl('scanBtn');
        this.convertBtn = getEl('convertBtn');
        this.stopBtn = getEl('stopBtn');
        this.clearBtn = getEl('clearBtn');
        
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
        // Helper to add event listener safely
        const addListener = (el, event, handler) => {
            if (el) el.addEventListener(event, handler);
        };
        
        // Browse button
        addListener(this.browseBtn, 'click', () => this.openBrowser());
        addListener(this.scanPathInput, 'click', () => this.openBrowser());
        
        // Action buttons
        addListener(this.scanBtn, 'click', () => this.startScan());
        addListener(this.convertBtn, 'click', () => this.startConvert());
        addListener(this.stopBtn, 'click', () => this.stopProcess());
        addListener(this.clearBtn, 'click', () => this.clearTerminal());
        
        // Settings changes
        addListener(this.scanDepthInput, 'change', () => this.saveSettings());
        addListener(this.safeModeCheckbox, 'change', () => this.saveSettings());
        addListener(this.includeSimpleCheckbox, 'change', () => this.saveSettings());
        addListener(this.autoCleanupCheckbox, 'change', () => this.saveSettings());
        
        // Jellyfin settings
        addListener(this.useJellyfinCheckbox, 'change', () => {
            this.saveSettings();
            this.updateScanModeIndicator();
        });
        addListener(this.jellyfinUrlInput, 'change', () => this.saveSettings());
        addListener(this.jellyfinApiKeyInput, 'change', () => this.saveSettings());
        addListener(this.toggleApiKeyBtn, 'click', () => this.toggleApiKeyVisibility());
        addListener(this.testJellyfinBtn, 'click', () => this.testJellyfinConnection());
        
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
            
            // Close tooltip when clicking elsewhere
            document.addEventListener('click', () => {
                const existing = document.querySelector('.tooltip-popup');
                if (existing) existing.remove();
            });
        } catch (e) {
            console.error('Error initializing tooltips:', e);
        }
    }
    
    showTooltipPopup(element, text) {
        // Remove any existing popup
        const existing = document.querySelector('.tooltip-popup');
        if (existing) existing.remove();
        
        const popup = document.createElement('div');
        popup.className = 'tooltip-popup';
        popup.textContent = text;
        document.body.appendChild(popup);
        
        const rect = element.getBoundingClientRect();
        popup.style.top = `${rect.bottom + 8}px`;
        popup.style.left = `${rect.left - 100}px`;
        
        // Auto-hide after 3 seconds
        setTimeout(() => popup.remove(), 3000);
    }
    
    connectWebSocket() {
        try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
            
            console.log('Connecting to WebSocket:', wsUrl);
            
            // Only append to terminal if it exists
            if (this.terminalContent) {
                this.appendToTerminal(`🔌 Connecting to ${wsUrl}...\n`, 'system');
            }
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
                console.log('WebSocket connected successfully');
                this.reconnectAttempts = 0;
                if (this.terminalContent) {
                    this.appendToTerminal('🔗 Connected to server\n', 'system');
                }
                
                // Start keepalive ping every 20 seconds
                this.startKeepalive();
        };
        
        this.ws.onmessage = (event) => {
                try {
            const data = JSON.parse(event.data);
                    // Ignore ping messages
                    if (data.type === 'ping' || data.type === 'keepalive') {
                        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                            this.ws.send('ping');
                        }
                        return;
                    }
            this.handleMessage(data);
                } catch (e) {
                    // Handle non-JSON messages (like "pong")
                    if (event.data === 'pong') {
                        return;
                    }
                    console.error('Failed to parse WebSocket message:', e);
                }
            };
            
            this.ws.onclose = (event) => {
                console.log('WebSocket disconnected:', event.code, event.reason);
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
            
            this.appendToTerminal(`🔄 Reconnecting in ${delay/1000}s (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...\n`, 'system');
            
            setTimeout(() => this.connectWebSocket(), delay);
        } else {
            this.appendToTerminal('❌ Failed to reconnect. Please refresh the page.\n', 'error');
        }
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'status':
                this.updateStatus(data.running, data.action);
                if (data.settings) {
                    this.applySettings(data.settings);
                }
                break;
            case 'output':
                this.appendToTerminal(data.data);
                break;
            case 'results':
                this.displayResults(data.data);
                break;
            case 'progress':
                this.updateProgress(data.data);
                break;
            case 'keepalive':
                // Ignore keepalive messages
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
            fill.style.width = `${progress.percent}%`;
            stats.textContent = `${progress.current} / ${progress.total}`;
            if (percent) percent.textContent = `${progress.percent}%`;
            detail.textContent = progress.filename ? `📄 ${progress.filename}` : 'Processing...';
            label.textContent = 'Scanning files...';
        } else if (progress.status === 'cancelled') {
            label.textContent = 'Cancelled';
            if (percent) percent.textContent = '—';
            detail.textContent = 'Scan was cancelled';
            setTimeout(() => {
                container.style.display = 'none';
            }, 3000);
        } else if (progress.status === 'complete') {
            fill.style.width = '100%';
            if (percent) percent.textContent = '100%';
            label.textContent = 'Complete!';
            detail.textContent = '✓ Scan finished successfully';
            setTimeout(() => {
                container.style.display = 'none';
            }, 3000);
        }
    }
    
    displayResults(results) {
        const summary = document.getElementById('resultsSummary');
        const countBadge = document.getElementById('resultCount');
        
        if (!summary) return;
        
        // Store results for pagination
        this.allResults = results;
        this.currentResultsPage = 1;
        
        const profile7 = results.profile7 || [];
        const profile8 = results.profile8 || [];
        
        // Update count badge
        if (countBadge) {
            countBadge.textContent = profile7.length > 0 ? profile7.length : '';
        }
        
        // Update summary
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
        
        // Render the first page
        this.renderResultsPage();
        
        // Switch to results tab
        this.switchTab('results');
    }
    
    renderResultsPage() {
        const list = document.getElementById('resultsList');
        const pagination = document.getElementById('pagination');
        const prevBtn = document.getElementById('prevPage');
        const nextBtn = document.getElementById('nextPage');
        const currentPageEl = document.getElementById('currentPage');
        const totalPagesEl = document.getElementById('totalPages');
        
        if (!list || !this.allResults) return;
        
        const profile7 = this.allResults.profile7 || [];
        const profile8 = this.allResults.profile8 || [];
        
        // Combine all DV files for pagination
        const allFiles = [
            ...profile7.map(f => ({ ...f, type: 'convert' })),
            ...profile8.map(f => ({ ...f, type: 'compatible' }))
        ];
        
        const totalItems = allFiles.length;
        const totalPages = Math.ceil(totalItems / this.resultsPerPage);
        
        // Ensure current page is valid
        if (this.currentResultsPage > totalPages) this.currentResultsPage = totalPages;
        if (this.currentResultsPage < 1) this.currentResultsPage = 1;
        
        // Calculate slice indices
        const startIndex = (this.currentResultsPage - 1) * this.resultsPerPage;
        const endIndex = Math.min(startIndex + this.resultsPerPage, totalItems);
        const pageItems = allFiles.slice(startIndex, endIndex);
        
        // Clear and render list
        list.innerHTML = '';
        
        if (totalItems === 0) {
            list.innerHTML = '<p class="no-results">No Dolby Vision files found.</p>';
            if (pagination) pagination.style.display = 'none';
            return;
        }
        
        pageItems.forEach(file => {
            const item = document.createElement('div');
            item.className = 'result-item';
            const badgeClass = file.type === 'convert' ? 'convert' : 'compatible';
            const badgeText = file.type === 'convert' ? 'Needs Conversion' : 'Compatible';
            
            item.innerHTML = `
                <div class="file-info">
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-meta">${file.hdr || file.profile || 'Dolby Vision'}</div>
                </div>
                <div class="file-action">
                    <span class="badge ${badgeClass}">${badgeText}</span>
                </div>
            `;
            list.appendChild(item);
        });
        
        // Update pagination controls
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
        const profile7 = this.allResults?.profile7 || [];
        const profile8 = this.allResults?.profile8 || [];
        const totalItems = profile7.length + profile8.length;
        const totalPages = Math.ceil(totalItems / this.resultsPerPage);
        
        if (page >= 1 && page <= totalPages) {
            this.currentResultsPage = page;
            this.renderResultsPage();
            
            // Scroll results list to top
            const list = document.getElementById('resultsList');
            if (list) list.scrollTop = 0;
        }
    }
    
    switchTab(tabName) {
        const tabs = document.querySelectorAll('.tab-btn');
        const contents = document.querySelectorAll('.tab-content');
        
        tabs.forEach(tab => {
            tab.classList.toggle('active', tab.dataset.tab === tabName);
        });
        
        contents.forEach(content => {
            content.classList.toggle('active', content.id === tabName + 'Tab');
        });
    }

    updateStatus(running, action = '') {
        this.isRunning = running;
        
        if (running) {
            this.statusIndicator.classList.add('running');
            this.statusText.textContent = action === 'scan' ? 'Scanning...' : 'Converting...';
            this.scanBtn.disabled = true;
            this.convertBtn.disabled = true;
            this.stopBtn.disabled = false;
        } else {
            this.statusIndicator.classList.remove('running');
            this.statusText.textContent = 'Ready';
            this.scanBtn.disabled = false;
            this.convertBtn.disabled = false;
            this.stopBtn.disabled = true;
            
            // Hide progress bar when done
            const container = document.getElementById('progressContainer');
            if (container) {
                const fill = document.getElementById('progressFill');
                const label = document.getElementById('progressLabel');
                if (fill) fill.style.width = '100%';
                if (label) label.textContent = 'Complete!';
                setTimeout(() => {
                    container.style.display = 'none';
                }, 1500);
            }
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
    
    // State persistence methods
    saveState() {
        try {
            const state = {
                logHistory: this.logHistory.slice(-this.maxLogLines),
                timestamp: Date.now()
            };
            sessionStorage.setItem('doviConvertState', JSON.stringify(state));
        } catch (e) {
            console.warn('Failed to save state:', e);
        }
    }
    
    restoreState() {
        try {
            const saved = sessionStorage.getItem('doviConvertState');
            if (saved) {
                const state = JSON.parse(saved);
                // Only restore if less than 1 hour old
                if (Date.now() - state.timestamp < 3600000) {
                    this.logHistory = state.logHistory || [];
                    // Restore log to terminal
                    if (this.logHistory.length > 0 && this.terminalContent) {
                        this.terminalContent.innerHTML = '';
                        this.logHistory.forEach(entry => {
                            this.appendToTerminalDirect(entry.text, entry.type);
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
        if (settings.scan_path) {
            this.scanPathInput.value = settings.scan_path;
            this.currentPath = settings.scan_path;
        }
        if (settings.scan_depth !== undefined) {
            this.scanDepthInput.value = settings.scan_depth;
        }
        if (settings.safe_mode !== undefined) {
            this.safeModeCheckbox.checked = settings.safe_mode;
        }
        if (settings.include_simple_fel !== undefined) {
            this.includeSimpleCheckbox.checked = settings.include_simple_fel;
        }
        if (settings.auto_cleanup !== undefined) {
            this.autoCleanupCheckbox.checked = settings.auto_cleanup;
        }
        
        // Jellyfin settings
        if (settings.use_jellyfin !== undefined && this.useJellyfinCheckbox) {
            this.useJellyfinCheckbox.checked = settings.use_jellyfin;
        }
        if (settings.jellyfin_url && this.jellyfinUrlInput) {
            this.jellyfinUrlInput.value = settings.jellyfin_url;
        }
        if (settings.jellyfin_api_key && this.jellyfinApiKeyInput) {
            this.jellyfinApiKeyInput.value = settings.jellyfin_api_key;
        }
        
        // Update the scan mode indicator
        this.updateScanModeIndicator();
    }
    
    async saveSettings() {
        const settings = {
            scan_path: this.scanPathInput.value,
            scan_depth: parseInt(this.scanDepthInput.value, 10),
            safe_mode: this.safeModeCheckbox.checked,
            include_simple_fel: this.includeSimpleCheckbox.checked,
            auto_cleanup: this.autoCleanupCheckbox.checked,
            use_jellyfin: this.useJellyfinCheckbox?.checked || false,
            jellyfin_url: this.jellyfinUrlInput?.value || '',
            jellyfin_api_key: this.jellyfinApiKeyInput?.value || ''
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
            const type = this.jellyfinApiKeyInput.type;
            this.jellyfinApiKeyInput.type = type === 'password' ? 'text' : 'password';
        }
    }
    
    async testJellyfinConnection() {
        if (!this.jellyfinStatus) return;
        
        const url = this.jellyfinUrlInput?.value;
        const apiKey = this.jellyfinApiKeyInput?.value;
        
        if (!url || !apiKey) {
            this.jellyfinStatus.className = 'jellyfin-status error';
            this.jellyfinStatus.textContent = 'Please enter Jellyfin URL and API key';
            return;
        }
        
        // Save settings first
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
        
        // Ensure WebSocket is connected before starting scan
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.appendToTerminal('🔌 Reconnecting WebSocket...\n', 'system');
            this.connectWebSocket();
            // Wait a moment for connection
            await new Promise(resolve => setTimeout(resolve, 1000));
        }
        
        this.appendToTerminal('📡 Sending scan request...\n', 'system');
        
        try {
            const response = await fetch('/api/scan', { method: 'POST' });
            const data = await response.json();
            
            if (!response.ok) {
                this.appendToTerminal(`❌ Server error: ${data.detail || response.statusText}\n`, 'error');
                return;
            }
            
            this.appendToTerminal(`✅ Scan started: ${JSON.stringify(data)}\n`, 'system');
        } catch (error) {
            this.appendToTerminal(`❌ Failed to start scan: ${error.message}\n`, 'error');
            console.error('Scan error:', error);
        }
    }
    
    async startConvert() {
        this.clearTerminal();
        try {
            await fetch('/api/convert', { method: 'POST' });
        } catch (error) {
            this.appendToTerminal(`❌ Failed to start conversion: ${error.message}\n`, 'error');
        }
    }
    
    async stopProcess() {
        try {
            await fetch('/api/stop', { method: 'POST' });
        } catch (error) {
            this.appendToTerminal(`❌ Failed to stop process: ${error.message}\n`, 'error');
        }
    }

    appendToTerminal(text, type = 'normal') {
        // Save to history
        this.logHistory.push({ text, type, time: Date.now() });
        if (this.logHistory.length > this.maxLogLines) {
            this.logHistory.shift();
        }
        this.saveState();
        
        // Render to terminal
        this.appendToTerminalDirect(text, type);
    }
    
    appendToTerminalDirect(text, type = 'normal') {
        if (!this.terminalContent) return;
        
        // Remove welcome message on first real output
        const welcomeMsg = this.terminalContent.querySelector('.welcome-msg');
        if (welcomeMsg) {
            this.terminalContent.innerHTML = '';
        }
        
        const span = document.createElement('span');
        span.textContent = text;
        
        if (type === 'error') {
            span.style.color = 'var(--accent-danger)';
        } else if (type === 'system') {
            span.style.color = 'var(--text-muted)';
        }
        
        // Apply colors based on content
        if (text.includes('✅') || text.includes('SUCCESS')) {
            span.style.color = 'var(--accent-secondary)';
        } else if (text.includes('❌') || text.includes('ERROR') || text.includes('FAIL')) {
            span.style.color = 'var(--accent-danger)';
        } else if (text.includes('⚠️') || text.includes('WARNING')) {
            span.style.color = 'var(--accent-warning)';
        } else if (text.includes('🔍') || text.includes('🎬')) {
            span.style.color = 'var(--accent-primary)';
        }
        
        this.terminalContent.appendChild(span);
        
        // Auto-scroll to bottom
        this.terminalContent.scrollTop = this.terminalContent.scrollHeight;
    }

    clearTerminal() {
        if (this.terminalContent) {
            this.terminalContent.innerHTML = '';
        }
        this.logHistory = [];
        this.saveState();
    }

    async openBrowser() {
        this.modal.classList.add('active');
        await this.loadDirectory(this.currentPath);
    }

    closeModal() {
        this.modal.classList.remove('active');
    }

    async loadDirectory(path) {
        try {
            const response = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            
            this.currentPath = data.current;
            this.currentPathDisplay.textContent = data.current;
            
            this.directoryList.innerHTML = '';
            
            // Add parent directory option
            if (data.parent) {
                const parentItem = this.createDirectoryItem('..', data.parent, true);
                this.directoryList.appendChild(parentItem);
            }
            
            // Add subdirectories
            for (const dir of data.directories) {
                const item = this.createDirectoryItem(dir.name, dir.path);
                this.directoryList.appendChild(item);
            }
            
            if (data.directories.length === 0 && !data.parent) {
                this.directoryList.innerHTML = '<p style="color: var(--text-muted); padding: 1rem;">No subdirectories found</p>';
            }
        } catch (error) {
            console.error('Failed to browse directory:', error);
            this.directoryList.innerHTML = '<p style="color: var(--accent-danger); padding: 1rem;">Failed to load directory</p>';
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
        this.scanPathInput.value = this.currentPath;
        this.saveSettings();
        this.closeModal();
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing app...');
    try {
        window.app = new DoViConvertApp();
        console.log('App initialized successfully');
    } catch (error) {
        console.error('Failed to initialize app:', error);
        document.body.innerHTML = `<pre style="color: red; padding: 20px;">Error initializing app: ${error.message}\n\n${error.stack}</pre>`;
    }
});
