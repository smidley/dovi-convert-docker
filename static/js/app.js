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
        
        console.log('Initializing elements...');
        this.initElements();
        console.log('Initializing event listeners...');
        this.initEventListeners();
        console.log('Connecting WebSocket...');
        this.connectWebSocket();
        console.log('Loading settings...');
        this.loadSettings();
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
        
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => this.switchTab(btn.dataset.tab));
        });
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
        
        if (!container) return;
        
        if (progress.status === 'scanning') {
            container.style.display = 'block';
            fill.style.width = `${progress.percent}%`;
            stats.textContent = `${progress.current} / ${progress.total}`;
            detail.textContent = progress.filename || '';
            label.textContent = 'Scanning files...';
        } else if (progress.status === 'cancelled') {
            label.textContent = 'Cancelled';
            setTimeout(() => {
                container.style.display = 'none';
            }, 2000);
        } else if (progress.status === 'complete') {
            fill.style.width = '100%';
            label.textContent = 'Complete!';
            setTimeout(() => {
                container.style.display = 'none';
            }, 2000);
        }
    }
    
    displayResults(results) {
        const summary = document.getElementById('resultsSummary');
        const list = document.getElementById('resultsList');
        const countBadge = document.getElementById('resultCount');
        
        if (!summary || !list) return;
        
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
        
        // Update list
        list.innerHTML = '';
        
        if (profile7.length === 0 && profile8.length === 0) {
            list.innerHTML = '<p class="no-results">No Dolby Vision files found.</p>';
            return;
        }
        
        // Show Profile 7 files first (need conversion)
        profile7.forEach(file => {
            const item = document.createElement('div');
            item.className = 'result-item';
            item.innerHTML = `
                <div class="file-info">
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-meta">${file.hdr}</div>
                </div>
                <div class="file-action">
                    <span class="badge convert">Needs Conversion</span>
                </div>
            `;
            list.appendChild(item);
        });
        
        // Show Profile 8 files (already compatible)
        profile8.forEach(file => {
            const item = document.createElement('div');
            item.className = 'result-item';
            item.innerHTML = `
                <div class="file-info">
                    <div class="file-name" title="${file.name}">${file.name}</div>
                    <div class="file-meta">${file.hdr}</div>
                </div>
                <div class="file-action">
                    <span class="badge compatible">Compatible</span>
                </div>
            `;
            list.appendChild(item);
        });
        
        // Switch to results tab
        this.switchTab('results');
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
    }
    
    async saveSettings() {
        const settings = {
            scan_path: this.scanPathInput.value,
            scan_depth: parseInt(this.scanDepthInput.value, 10),
            safe_mode: this.safeModeCheckbox.checked,
            include_simple_fel: this.includeSimpleCheckbox.checked,
            auto_cleanup: this.autoCleanupCheckbox.checked
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
        this.terminalContent.innerHTML = '';
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
