/**
 * VG 豆包生产一键填词与参考图助手 - 原生 Chrome 扩展 (v2.0 极简闭环版)
 * 1. 动态标题指纹 ([V01] 豆包) 强绑定窗口，支持工作台一键秒切置顶
 * 2. 智能激活注入 (前台即时导入 / 后台切入瞬间导入)，彻底根治后台抢焦点丢图丢词
 * 3. 任务与会话穿透持久化 (sessionStorage + Tab 注册表)，成片精准溯源
 * 4. HUD 悬浮面板集成「📥 下载视频」原生一键下载，直存浏览器 Downloads 目录
 * 5. 全链路支持上限 10 张参考图 Base64 直送，严禁截断
 */

(function() {
    'use strict';

    const API_BASE = 'http://127.0.0.1:8765';
    let currentTask = null;
    let autoInjected = false;
    let importInProgress = false;
    let latestVideoUrl = '';
    let titleWatcherTimer = null;
    let taskPromptSentTime = 0;

    console.log('[VG Extension 2.0] 豆包原生全自动生产助手启动');

    // 解析当前 URL、Hash 或 sessionStorage 中的任务 ID
    function getUrlTaskId() {
        try {
            const params = new URLSearchParams(window.location.search);
            if (params.get('vg_task')) return params.get('vg_task');

            const hash = window.location.hash.replace(/^#/, '');
            const hashParams = new URLSearchParams(hash);
            if (hashParams.get('vg_task')) return hashParams.get('vg_task');

            const m = window.location.href.match(/vg_task=([A-Za-z0-9_-]+)/);
            if (m) return m[1];

            // SPA 路由跳转后从 sessionStorage 恢复穿透
            const cached = sessionStorage.getItem('vg_task_id');
            if (cached) return cached;
        } catch (e) {}
        return '';
    }

    // 从本地工作台服务拉取任务上下文
    async function fetchTaskContext(taskId) {
        let url = `${API_BASE}/api/task/context`;
        if (taskId) {
            url += `?task_id=${encodeURIComponent(taskId)}`;
        } else {
            url += `?latest=1`;
        }

        try {
            const resp = await fetch(url);
            const data = await resp.json();
            if (data.code === 0 && data.data) {
                return data.data;
            }
        } catch (e) {
            console.warn('[VG Extension] 拉取工作台任务上下文失败:', e);
        }
        return null;
    }

    // 动态注入并锁定网页标题指纹 [V01] 豆包 - 用于 Windows 瞬间置顶精准切窗
    function setupTabTitleFingerprint(task) {
        if (!task || !task.id) return;
        const prefix = `[${task.id}] 豆包`;
        const applyTitle = () => {
            if (!document.title.startsWith(`[${task.id}]`)) {
                const subTitle = task.title ? ` - ${task.title}` : '';
                document.title = `${prefix}${subTitle}`;
            }
        };
        applyTitle();
        if (titleWatcherTimer) clearInterval(titleWatcherTimer);
        titleWatcherTimer = setInterval(applyTitle, 1500);
    }

    // 向 Background 注册 Tab 任务身份映射
    function registerTabWithBackground(task) {
        if (!task || !task.id) return;
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
            chrome.runtime.sendMessage({
                type: 'REGISTER_TAB_TASK',
                taskId: task.id,
                account: task.account || ''
            }).catch(() => {});
        }
    }

    // 向服务端回传具体对话会话地址 (用于断点切窗)
    async function reportSessionUrl(task) {
        if (!task || !task.id) return;
        const payload = {
            task_id: task.id,
            session_url: window.location.href,
            account_id: task.account || ''
        };
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
            chrome.runtime.sendMessage({
                type: 'BIND_SESSION',
                payload: payload
            }).catch(() => {});
        }
        try {
            await fetch(`${API_BASE}/api/task/bind_session`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } catch (e) {}
    }

    // 寻找豆包输入框 (优先 ProseMirror / tiptap)
    function findChatInput() {
        return document.querySelector('div.ProseMirror') ||
               document.querySelector('[data-testid="chat_input_input"] [contenteditable="true"]') ||
               document.querySelector('[data-testid="chat_input"] [contenteditable="true"]') ||
               document.querySelector('div[contenteditable="true"]') ||
               document.querySelector('textarea');
    }

    // Base64 转 File 对象
    function b64ToFile(b64Data, filename) {
        try {
            const arr = b64Data.split(',');
            const mime = arr[0].match(/:(.*?);/)[1];
            const bstr = atob(arr[1]);
            let n = bstr.length;
            const u8arr = new Uint8Array(n);
            while (n--) {
                u8arr[n] = bstr.charCodeAt(n);
            }
            return new File([u8arr], filename || 'reference.png', { type: mime });
        } catch (e) {
            console.error('[VG Extension] Base64 转 File 失败:', e);
            return null;
        }
    }

    // 获取图片 File 对象 (优先 Base64 直转，降级 fetch blob)
    async function fetchImageFile(imgObj) {
        if (imgObj.base64) {
            const f = b64ToFile(imgObj.base64, imgObj.name || imgObj.filename);
            if (f) return f;
        }

        try {
            const resp = await fetch(imgObj.url);
            const blob = await resp.blob();
            return new File([blob], imgObj.filename || imgObj.name || 'reference.png', {
                type: blob.type || 'image/png'
            });
        } catch (e) {
            console.warn('[VG Extension] fetchImageFile 失败:', imgObj.name, e);
            return null;
        }
    }

    // 精准填入提示词正文 (靶向适配 ProseMirror / Tiptap)
    function injectPromptText(editorEl, text) {
        if (!editorEl || !text) return false;
        editorEl.focus();

        if (editorEl.tagName.toLowerCase() === 'textarea') {
            editorEl.value = text;
            editorEl.dispatchEvent(new Event('input', { bubbles: true }));
            editorEl.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }

        // ProseMirror 富文本编辑器靶向输入
        try {
            // 1. 设置选区
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(editorEl);
            sel.removeAllRanges();
            sel.addRange(range);

            // 2. 派发 DataTransfer Paste 事件 (ProseMirror 核心原生入口)
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            const pasteEvt = new ClipboardEvent('paste', {
                clipboardData: dt,
                bubbles: true,
                cancelable: true
            });
            editorEl.dispatchEvent(pasteEvt);

            // 3. 检查是否填入成功
            if (editorEl.innerText && editorEl.innerText.trim().length > 5) {
                console.log('[VG Extension] Paste 事件成功注入文本');
                return true;
            }
        } catch (e) {
            console.warn('[VG Extension] Paste 注入报错:', e);
        }

        // 备用方案: beforeinput
        try {
            const inputEvt = new InputEvent('beforeinput', {
                inputType: 'insertText',
                data: text,
                bubbles: true,
                cancelable: true
            });
            editorEl.dispatchEvent(inputEvt);
            if (editorEl.innerText && editorEl.innerText.trim().length > 5) {
                return true;
            }
        } catch (e) {}

        // 备用方案 2: execCommand
        try {
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, text);
            editorEl.dispatchEvent(new InputEvent('input', { bubbles: true }));
            if (editorEl.innerText && editorEl.innerText.trim().length > 5) {
                return true;
            }
        } catch (e) {}

        return false;
    }

    // 自动上传参考图片 (通过 Paste 派发给 ProseMirror，上限全量 10 张，严禁截断)
    async function uploadReferenceImages(editorEl, images) {
        if (!images || images.length === 0 || !editorEl) return 0;
        
        // 优先选取提示词中明确命中的所有图片（最多10张）；若未提及则兜底取前 10 张主要参考图
        const matchedImgs = images.filter(img => img.matched);
        const targetImgs = matchedImgs.length > 0 ? matchedImgs.slice(0, 10) : images.slice(0, 10);

        let successCount = 0;
        for (const img of targetImgs) {
            try {
                const file = await fetchImageFile(img);
                if (!file) continue;

                editorEl.focus();
                const dt = new DataTransfer();
                dt.items.add(file);

                const pasteEvt = new ClipboardEvent('paste', {
                    clipboardData: dt,
                    bubbles: true,
                    cancelable: true
                });
                editorEl.dispatchEvent(pasteEvt);
                successCount++;
                console.log('[VG Extension] 成功粘贴参考图:', img.name);
                await new Promise(r => setTimeout(r, 450));
            } catch (err) {
                console.warn('[VG Extension] 上传参考图异常:', img.name, err);
            }
        }

        return successCount;
    }

    // 标记工作台任务为已发豆包，并扣减额度
    async function markTaskSent(task) {
        if (!task || !task.id) return;
        const payload = {
            task_id: task.id,
            step: 'sent',
            value: true,
            account_id: task.account || ''
        };

        try {
            await fetch(`${API_BASE}/api/task/mark_step`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            console.log('[VG Extension] 工作台已标记已发送');
        } catch (e) {}
    }

    // 执行全自动导入主逻辑 (带 DOM 双重就绪校验)
    async function executeAutoImport(silent = false) {
        if (importInProgress) return;
        importInProgress = true;

        try {
            if (!currentTask) {
                const taskId = getUrlTaskId();
                currentTask = await fetchTaskContext(taskId);
            }

            if (!currentTask) {
                if (!silent) alert('未检测到当前流水线任务，请确认工作台服务已启动！');
                updateHUDStatus('未检测到任务', '#ef4444');
                importInProgress = false;
                return;
            }

            const inputEl = findChatInput();
            if (!inputEl) {
                if (!silent) alert('未找到豆包输入框，请等待页面完全载入后重试！');
                updateHUDStatus('等待输入框载入...', '#f59e0b');
                importInProgress = false;
                return;
            }

            updateHUDStatus('⚡ 正在自动填入提示词...', '#3b82f6');
            injectPromptText(inputEl, currentTask.prompt);

            // 复制提示词到系统剪贴板保底 (若有焦点)
            if (currentTask.prompt && navigator.clipboard && document.hasFocus()) {
                navigator.clipboard.writeText(currentTask.prompt).catch(() => {});
            }

            // 等待 500ms 让 ProseMirror 完成文本状态更新，再注入图片
            if (currentTask.ref_images && currentTask.ref_images.length > 0) {
                updateHUDStatus('🖼️ 正在导入参考图...', '#3b82f6');
                await new Promise(r => setTimeout(r, 500));
                await uploadReferenceImages(inputEl, currentTask.ref_images);
            }

            // 文本注入校验
            if (inputEl.innerText && inputEl.innerText.trim().length > 5) {
                updateHUDStatus('✅ 词与图已自动导入就绪！', '#10b981');
                await markTaskSent(currentTask);
                autoInjected = true;
                taskPromptSentTime = Date.now();
                if (currentTask && currentTask.id) {
                    sessionStorage.setItem(`vg_injected_${currentTask.id}`, '1');
                    sessionStorage.setItem(`vg_sent_time_${currentTask.id}`, String(taskPromptSentTime));
                }

                // 绑定真实发送动作（点击发送按钮或回车）
                const bindSendAction = () => {
                    taskPromptSentTime = Date.now();
                    if (currentTask && currentTask.id) {
                        sessionStorage.setItem(`vg_sent_time_${currentTask.id}`, String(taskPromptSentTime));
                    }
                    updateHUDStatus('🔵 豆包生成中，自动监控成片...', '#3b82f6');
                };

                const sendBtn = document.querySelector('[data-testid="chat_input_send_button"]') ||
                                document.querySelector('button[aria-label*="发送"]') ||
                                document.querySelector('button[type="submit"]');
                if (sendBtn && !sendBtn.dataset.vgBound) {
                    sendBtn.dataset.vgBound = '1';
                    sendBtn.addEventListener('click', bindSendAction);
                }
                if (!inputEl.dataset.vgBound) {
                    inputEl.dataset.vgBound = '1';
                    inputEl.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter' && !e.shiftKey) bindSendAction();
                    });
                }
            } else {
                updateHUDStatus('⚠️ 提示词未成功填入，请点击重试', '#f59e0b');
            }
        } catch (e) {
            console.error('[VG Extension] 导入失败:', e);
            updateHUDStatus('导入发生异常，可手动点击', '#ef4444');
        } finally {
            importInProgress = false;
        }
    }

    // 更新悬浮控制面板状态文字
    function updateHUDStatus(text, color = '#60a5fa') {
        const statusEl = document.getElementById('vg-hud-status');
        if (statusEl) {
            statusEl.innerText = text;
            statusEl.style.color = color;
        }
    }

    // 复制单张图片到剪贴板
    async function copyImageToClipboard(imgObj) {
        try {
            const file = await fetchImageFile(imgObj);
            if (!file) {
                alert('获取图片失败');
                return;
            }
            await navigator.clipboard.write([
                new ClipboardItem({ [file.type]: file })
            ]);
            updateHUDStatus(`已复制图片 ${imgObj.name}，可直接 Ctrl+V！`, '#10b981');
        } catch (err) {
            alert('复制图片到剪贴板失败: ' + err);
        }
    }

    // 触发 Chrome 原生下载保存成片
    function triggerNativeDownload() {
        if (!latestVideoUrl) {
            alert('暂未捕获到无水印成片链接！');
            return;
        }
        const taskId = currentTask ? currentTask.id : 'VG';
        const taskTitle = currentTask ? (currentTask.title || '成片') : '成片';
        const safeTitle = taskTitle.replace(/[\\/*?:"<>|]/g, '_');
        const filename = `[${taskId}]_${safeTitle}.mp4`;

        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
            chrome.runtime.sendMessage({
                type: 'DOWNLOAD_MEDIA',
                url: latestVideoUrl,
                filename: filename
            }, (res) => {
                if (res && res.success) {
                    updateHUDStatus(`✅ 已触发下载: ${filename}！`, '#10b981');
                } else {
                    window.open(latestVideoUrl, '_blank');
                }
            });
        } else {
            window.open(latestVideoUrl, '_blank');
        }
    }

    // 创建右下角悬浮控制台 HUD (集成原生下载按钮)
    function renderHUD(task) {
        let old = document.getElementById('vg-assistant-hud');
        if (old) old.remove();

        const hud = document.createElement('div');
        hud.id = 'vg-assistant-hud';
        hud.style.cssText = `
            position: fixed;
            bottom: 25px;
            right: 25px;
            z-index: 99999999;
            width: 330px;
            background: #181824;
            color: #e2e8f0;
            border-radius: 12px;
            border: 1px solid #3b82f6;
            box-shadow: 0 10px 30px rgba(0,0,0,0.6);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            font-size: 13px;
            overflow: hidden;
            transition: all 0.2s ease;
        `;

        let refImgsHtml = '';
        const displayImgs = (task && task.ref_images) ? (
            task.ref_images.filter(img => img.matched).length > 0
                ? task.ref_images.filter(img => img.matched).slice(0, 10)
                : task.ref_images.filter(img => img.top_level).slice(0, 10)
        ) : [];

        if (displayImgs.length > 0) {
            refImgsHtml = `
                <div style="margin-top: 8px;">
                    <div style="font-size: 11px; color: #94a3b8; margin-bottom: 4px;">关联参考图 (${displayImgs.length}张，点击可直拷)：</div>
                    <div style="display: flex; gap: 6px; overflow-x: auto; padding-bottom: 4px;">
                        ${displayImgs.map((img, i) => `
                            <div style="background: #232336; border: 1px solid #3b4261; border-radius: 6px; padding: 4px; text-align: center; cursor: pointer; flex-shrink: 0;" id="vg-img-item-${i}" title="点击直接复制图片: ${img.name}">
                                <img src="${img.base64 || img.url}" style="width: 52px; height: 52px; object-fit: cover; border-radius: 4px; display: block;" />
                                <div style="font-size: 10px; color: #cbd5e1; margin-top: 2px; max-width: 52px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${img.name}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        const showDownloadBtn = !!(latestVideoUrl || (task && task.video_url));
        if (!latestVideoUrl && task && task.video_url) {
            latestVideoUrl = task.video_url;
        }

        hud.innerHTML = `
            <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 10px 14px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; cursor: pointer;" id="vg-hud-header">
                <span style="display: flex; align-items: center; gap: 6px;">
                    ⚡ VG 原生助手 ${task ? `<span style="background: #fbbf24; color: #000; font-size: 11px; padding: 1px 6px; border-radius: 4px; font-weight: bold;">${task.id}</span>` : ''}
                </span>
                <span id="vg-hud-toggle" style="font-size: 14px;">▼</span>
            </div>
            <div id="vg-hud-content" style="padding: 12px 14px;">
                <div style="font-weight: 600; color: #f8fafc; font-size: 13px; line-height: 1.4; margin-bottom: 4px;">
                    ${task ? task.title : '未绑定任务 (点击刷新)'}
                </div>
                <div id="vg-hud-status" style="font-size: 12px; color: #60a5fa; margin-bottom: 8px;">
                    🟢 已就绪 (等待就绪)
                </div>
                ${refImgsHtml}
                <div style="margin-top: 10px; display: flex; gap: 6px; flex-wrap: wrap;">
                    <button id="vg-btn-import" style="flex: 1; background: #2563eb; color: #fff; border: none; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; box-shadow: 0 2px 8px rgba(37,99,235,0.4);">
                        🚀 重新自动导入
                    </button>
                    <button id="vg-btn-download" style="display: ${showDownloadBtn ? 'inline-flex' : 'none'}; align-items: center; justify-content: center; background: #059669; color: #fff; border: none; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; box-shadow: 0 2px 8px rgba(5,150,105,0.4);" title="直接通过 Chrome 下载管理器保存到系统 Downloads">
                        📥 下载视频
                    </button>
                    <button id="vg-btn-copy" style="background: #334155; color: #cbd5e1; border: none; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 12px;">
                        📋 拷词
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(hud);

        // 绑定参考图点击复制事件
        displayImgs.forEach((img, i) => {
            const el = document.getElementById(`vg-img-item-${i}`);
            if (el) el.addEventListener('click', () => copyImageToClipboard(img));
        });

        // 按钮事件
        document.getElementById('vg-btn-import').addEventListener('click', () => executeAutoImport(false));
        document.getElementById('vg-btn-download').addEventListener('click', triggerNativeDownload);
        document.getElementById('vg-btn-copy').addEventListener('click', async () => {
            if (task && task.prompt && navigator.clipboard) {
                await navigator.clipboard.writeText(task.prompt);
                updateHUDStatus('提示词已复制到剪贴板！可直接 Ctrl+V', '#10b981');
            }
        });

        // 折叠展开
        let isCollapsed = false;
        document.getElementById('vg-hud-header').addEventListener('click', (e) => {
            if (e.target.id === 'vg-btn-import' || e.target.id === 'vg-btn-copy' || e.target.id === 'vg-btn-download') return;
            isCollapsed = !isCollapsed;
            document.getElementById('vg-hud-content').style.display = isCollapsed ? 'none' : 'block';
            document.getElementById('vg-hud-toggle').innerText = isCollapsed ? '▲' : '▼';
        });
    }

    // 初始化智能激活调度与生命周期绑定
    async function init() {
        const taskId = getUrlTaskId();
        currentTask = await fetchTaskContext(taskId);
        
        if (currentTask) {
            console.log('[VG Extension] 成功加载任务数据:', currentTask.id, currentTask.title);
            sessionStorage.setItem('vg_task_id', currentTask.id);
            setupTabTitleFingerprint(currentTask);
            registerTabWithBackground(currentTask);
            reportSessionUrl(currentTask);
            renderHUD(currentTask);

            // 监听路由变化，及时同步会话 URL
            let lastHref = window.location.href;
            setInterval(() => {
                if (window.location.href !== lastHref) {
                    lastHref = window.location.href;
                    reportSessionUrl(currentTask);
                }
            }, 3000);

            // 智能激活策略判断：防重复注入拦截
            const urlParams = new URLSearchParams(window.location.search);
            const isViewOnlyMode = urlParams.get('vg_view') === '1' || urlParams.get('view_only') === '1';
            const isAlreadySent = currentTask && (currentTask.sent || currentTask.status === 'generating' || currentTask.status === 'ready_video' || currentTask.status === 'downloaded');
            const isAlreadyInjected = currentTask && sessionStorage.getItem(`vg_injected_${currentTask.id}`) === '1';

            if (isViewOnlyMode || isAlreadySent || isAlreadyInjected) {
                console.log('[VG Extension] 当前为查看/已发送/已就绪状态，严禁重复注入提示词与参考图');
                updateHUDStatus(isAlreadySent ? '🎬 任务会话运行中 (监控中)' : '👁️ 查看/切窗模式', '#10b981');
                return;
            }

            const isTabForeground = () => document.visibilityState === 'visible' && document.hasFocus();

            // 轮询等待豆包输入框渲染
            let attempts = 0;
            const timer = setInterval(() => {
                attempts++;
                const inputEl = findChatInput();
                if (inputEl && !autoInjected) {
                    clearInterval(timer);

                    if (isTabForeground()) {
                        updateHUDStatus('🎯 前台窗口就绪，正在自动导入...', '#3b82f6');
                        setTimeout(() => executeAutoImport(true), 600);
                    } else {
                        updateHUDStatus('💤 后台待命 (切到此页面将自动秒级导入)', '#a855f7');
                        const onActivate = () => {
                            if (!autoInjected && !importInProgress) {
                                window.removeEventListener('focus', onActivate);
                                document.removeEventListener('visibilitychange', onActivate);
                                updateHUDStatus('🎯 窗口已激活，正在自动导入...', '#3b82f6');
                                setTimeout(() => executeAutoImport(true), 300);
                            }
                        };
                        window.addEventListener('focus', onActivate);
                        document.addEventListener('visibilitychange', () => {
                            if (document.visibilityState === 'visible') onActivate();
                        });
                    }
                }
                if (attempts > 50) {
                    clearInterval(timer);
                    updateHUDStatus('等待超时，请点击按钮手动导入');
                }
            }, 500);
        } else {
            console.log('[VG Extension] 未获取到本地流水线任务，渲染备用面板');
            renderHUD(null);
        }

        // 监听后台 ServiceWorker 截获并解密的无水印视频与切窗高亮消息
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
            chrome.runtime.onMessage.addListener(async (msg) => {
                if (msg && msg.type === 'WINDOW_SWITCHED') {
                    updateHUDStatus(`🎯 已切至任务 [${msg.taskId || ''}] 会话！`, '#10b981');
                }
                if (msg && msg.type === 'MEDIA_FOUND' && msg.items) {
                    const taskId = currentTask ? currentTask.id : getUrlTaskId();
                    const cachedSentTime = parseInt(sessionStorage.getItem(`vg_sent_time_${taskId}`) || '0', 10);
                    const sentTime = taskPromptSentTime || cachedSentTime;

                    // 严密生命周期契约：如果本任务尚未发送提示词，坚决忽略历史旧视频！
                    if (!sentTime) {
                        console.log('[VG Extension] 当前任务尚未发词，忽略页面初次加载拦截的历史旧视频');
                        return;
                    }

                    // 防误判核心铁律：豆包视频生成至少需要 15 秒以上！
                    // 若收到视频距离发词导入时间不足 15 秒，必为页面初次载入拉取的历史聊天旧视频，坚决忽略！
                    const now = Date.now();
                    const elapsed = now - sentTime;
                    if (elapsed < 15000) {
                        console.log(`[VG Extension] 忽略初次加载的历史旧视频 (距发词仅 ${Math.round(elapsed / 1000)}s < 15s)`);
                        return;
                    }

                    const videoItems = msg.items.filter(it => it.type === 'video');
                    for (const v of videoItems) {
                        console.log('[VG Extension] 成功捕获本次发词后生成的新成片:', v.url);
                        latestVideoUrl = v.url;
                        const dlBtn = document.getElementById('vg-btn-download');
                        if (dlBtn) dlBtn.style.display = 'inline-flex';
                        await reportVideoReady(v.url);
                    }
                }
            });
        }
    }

    // 上报无水印成片链接到 Web 工作台
    async function reportVideoReady(videoUrl) {
        const taskId = currentTask ? currentTask.id : getUrlTaskId();
        updateHUDStatus('🎉 已捕获无水印成片！正在同步工作台...');
        
        const payload = {
            task_id: taskId,
            video_url: videoUrl,
            account_id: (currentTask && currentTask.account) ? currentTask.account : ''
        };

        // 优先通过 service worker 代理请求，避开页面级 PNA / Mixed Content 阻断
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
            try {
                const res = await new Promise((resolve) => {
                    chrome.runtime.sendMessage({
                        type: 'REPORT_VIDEO',
                        payload: payload
                    }, (response) => {
                        if (chrome.runtime.lastError) {
                            resolve(null);
                        } else {
                            resolve(response);
                        }
                    });
                });

                if (res && res.success) {
                    const reportedTaskId = res.data?.task_id || taskId;
                    updateHUDStatus(`✅ 成片已绑定 [${reportedTaskId}]，可点击下载！`, '#10b981');
                    const dlBtn = document.getElementById('vg-btn-download');
                    if (dlBtn) dlBtn.style.display = 'inline-flex';
                    return;
                }
            } catch (err) {
                console.warn('[VG Extension] Service worker 上报异常，降级直连:', err);
            }
        }

        // 降级直连 fetch
        try {
            const resp = await fetch(`${API_BASE}/api/task/report_video`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            if (data.code === 0) {
                updateHUDStatus(`✅ 成片已绑定 [${data.task_id || taskId}]，可点击下载！`, '#10b981');
                const dlBtn = document.getElementById('vg-btn-download');
                if (dlBtn) dlBtn.style.display = 'inline-flex';
            }
        } catch (e) {
            console.warn('[VG Extension] 上报视频直连失败:', e);
        }
    }

    // 页面加载完成后立即执行
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
