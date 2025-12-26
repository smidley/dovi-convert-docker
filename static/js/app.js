/**
 * DoVi Convert Web Interface
 * Frontend JavaScript for real-time communication with the backend
 */

class DoViConvertApp {
    constructor() {
        this.ws = null;
        this.isRunning = false;
        this.currentPath = '/media';
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        
        this.initElements();
        this.initEventListeners();
        this.connectWebSocket();
        this.loadSettings();
    }

    initElements() {
        // Status
        this.statusIndicator = document.getElementById('statusIndicator');
        this.statusText = this.statusIndicator.querySelector('.status-text');
        
        // Controls
        this.scanPathInput = document.getElementById('scanPath');
        this.scanDepthInput = document.getElementById('scanDepth');
        this.safeModeCheckbox = document.getElementById('safeMode');
        this.includeSimpleCheckbox = document.getElementById('includeSimple');
        this.autoCleanupCheckbox = document.getElementById('autoCleanup');
        
        // Buttons
        this.browseBtn = document.getElementById('browseBtn');
        this.scanBtn = document.getElementById('scanBtn');
        this.convertBtn = document.getElementById('convertBtn');
        this.stopBtn = document.getElementById('stopBtn');
        this.clearBtn = document.getElementById('clearBtn');
        
        // Terminal
        this.terminal = document.getElementById('terminal');
        this.terminalContent = document.getElementById('terminalContent');
        
        // Modal
        this.modal = document.getElementById('browserModal');
        this.currentPathDisplay = document.getElementById('currentPath');
        this.directoryList = document.getElementById('directoryList');
        this.modalClose = document.getElementById('modalClose');
        this.modalCancel = document.getElementById('modalCancel');
        this.modalSelect = document.getElementById('modalSelect');
    }

    initEventListeners() {
        // Browse button
        this.browseBtn.addEventListener('click', () => this.openBrowser());
        this.scanPathInput.addEventListener('click', () => this.openBrowser());
        
        // Action buttons
        this.scanBtn.addEventListener('click', () => this.startScan());
        this.convertBtn.addEventListener('click', () => this.startConvert());
        this.stopBtn.addEventListener('click', () => this.stopProcess());
        this.clearBtn.addEventListener('click', () => this.clearTerminal());
        
        // Settings changes
        this.scanDepthInput.addEventListener('change', () => this.saveSettings());
        this.safeModeCheckbox.addEventListener('change', () => this.saveSettings());
        this.includeSimpleCheckbox.addEventListener('change', () => this.saveSettings());
        this.autoCleanupCheckbox.addEventListener('change', () => this.saveSettings());
        
        // Modal
        this.modalClose.addEventListener('click', () => this.closeModal());
        this.modalCancel.addEventListener('click', () => this.closeModal());
        this.modalSelect.addEventListener('click', () => this.selectDirectory());
        this.modal.querySelector('.modal-backdrop').addEventListener('click', () => this.closeModal());
        
        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.modal.classList.contains('active')) {
                this.closeModal();
            }
        });
        
        // Initialize tooltips
        this.initTooltips();
    }
    
    initTooltips() {
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
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;
        
        console.log('Connecting to WebSocket:', wsUrl);
        this.appendToTerminal(`🔌 Connecting to ${wsUrl}...\n`, 'system');
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
            this.appendToTerminal('🔗 Connected to server\n', 'system');
        };
        
        this.ws.onmessage = (event) => {
            console.log('WebSocket message:', event.data);
            try {
                const data = JSON.parse(event.data);
                this.handleMessage(data);
            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
                this.appendToTerminal(`Raw message: ${event.data}\n`, 'system');
            }
        };
        
        this.ws.onclose = (event) => {
            console.log('WebSocket disconnected:', event.code, event.reason);
            this.appendToTerminal(`⚠️ Disconnected (code: ${event.code})\n`, 'error');
            this.attemptReconnect();
        };
        
        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.appendToTerminal('❌ WebSocket connection error\n', 'error');
        };
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
        }
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
    window.app = new DoViConvertApp();
});
