/**
 * VG 豆包生产一键填词与参考图助手 - 原生 Chrome 扩展 (v2.0 极简闭环版)
 * 1. 动态标题指纹 ([V01] 豆包) 强绑定窗口，支持工作台一键秒切置顶
 * 2. 智能激活注入 (前台即时导入 / 后台切入瞬间导入)，彻底根治后台抢焦点丢图丢词
 * 3. 任务与会话穿透持久化 (sessionStorage + Tab 注册表)，成片精准溯源
 * 4. HUD 悬浮面板集成「📥 下载视频」原生一键下载，直存浏览器 Downloads 目录
 * 5. 脚本产品参考图全部 Base64 直送，不筛选、不截断
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

    // 如果是工作台的批量 Cookie 导出中间页，触发全自动提取并回传
    if (window.location.host === '127.0.0.1:8765' && window.location.pathname.includes('export_cookies.html')) {
        const params = new URLSearchParams(window.location.search);
        const profile = params.get('profile');
        if (profile) {
            chrome.runtime.sendMessage({ action: 'GET_DOUBAO_COOKIES' }, (res) => {
                if (res && res.cookies) {
                    fetch('/api/cookies/upload', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ profile: profile, cookies: res.cookies })
                    }).then(() => {
                        const statusEl = document.getElementById('status');
                        if (statusEl) statusEl.innerText = `✅ [${profile}] 提取成功 (${res.cookies.length} 条)，即将关闭...`;
                        setTimeout(() => window.close(), 600);
                    }).catch(e => {
                        console.error('Upload failed:', e);
                    });
                }
            });
        }
        return;
    }

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

    // 解析当前 URL 中的豆包会话 ID (/chat/123456789)
    function getCurrentConversationId() {
        try {
            const m = window.location.pathname.match(/\/chat\/([A-Za-z0-9_-]+)/);
            if (m && m[1] && m[1] !== 'new') return m[1];
        } catch (e) {}
        return '';
    }

    // 从本地工作台服务拉取任务上下文
    async function fetchTaskContext(taskId) {
        let url = `${API_BASE}/api/task/context`;
        if (taskId) {
            url += `?task_id=${encodeURIComponent(taskId)}`;
        } else {
            const sessUrl = window.location.href;
            url += `?session_url=${encodeURIComponent(sessUrl)}`;
        }

        try {
            const resp = await fetch(url);
            const data = await resp.json();
            if (data.code === 0 && data.data) {
                return data.data;
            }
            if (taskId) {
                updateHUDStatus(data.message || '读取任务失败', '#ef4444');
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
        const convId = getCurrentConversationId();
        if (convId) {
            sessionStorage.setItem(`vg_conv_${task.id}`, convId);
        }
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

    // 自动上传参考图片 (通过 Paste 派发给 ProseMirror，上传目录内全部图片)
    async function uploadReferenceImages(editorEl, images) {
        if (!images || images.length === 0 || !editorEl) return 0;
        
        // 上传当前脚本产品参考图目录中的全部图片。
        const targetImgs = images;

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

            if (!currentTask.prompt?.trim()) {
                updateHUDStatus('提示词为空，请检查任务文件路径', '#ef4444');
                currentTask = null;
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
            const textImported = injectPromptText(inputEl, currentTask.prompt);
            if (!textImported) {
                updateHUDStatus('提示词未成功填入，请点击重试', '#f59e0b');
                return;
            }

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
            if ((inputEl.value || inputEl.innerText || '').trim().length > 5) {
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
            // 联动回传服务端标记下载完成 (保持控制台打标同步)
            if (currentTask && currentTask.id) {
                fetch(`${API_BASE}/api/task/mark_step`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        task_id: currentTask.id,
                        step: 'downloaded',
                        value: true,
                        source: 'browser_native'
                    })
                }).then(() => {
                    updateHUDStatus(`💾 浏览器下载已触发，任务已完成！1.5秒后自动关闭...`, '#10b981');
                    setTimeout(() => {
                        try { window.close(); } catch (e) {}
                        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
                            chrome.runtime.sendMessage({ type: "CLOSE_CURRENT_TAB" });
                        }
                    }, 1500);
                }).catch(() => {});
            }
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
        const displayImgs = task?.ref_images || [];

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
            } else {
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
            }
        } else {
            console.log('[VG Extension] 未获取到本地流水线任务，渲染备用面板');
            renderHUD(null);
        }

        // 确保成片监听器已启动
        setupMessageListener();
    }

    // 独立常驻成片监听调度（顶层常驻，彻底免疫页面刷新与生命周期阻断）
    let messageListenerRegistered = false;
    function setupMessageListener() {
        if (messageListenerRegistered) return;
        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
            messageListenerRegistered = true;

            // 监听反向寻址匹配（供后台精准识别并关闭对应标签页）
            chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
                if (msg && msg.type === 'PING_TASK_MATCH') {
                    const myTaskId = currentTask ? currentTask.id : getUrlTaskId();
                    const myConvId = getCurrentConversationId();
                    const cachedConvId = myTaskId ? sessionStorage.getItem(`vg_conv_${myTaskId}`) : null;
                    let matched = false;
                    if (msg.taskId && myTaskId && (myTaskId === msg.taskId || myTaskId.startsWith(msg.taskId.split('_')[0]))) {
                        matched = true;
                    } else if (msg.sessionUrl) {
                        if (window.location.href.includes(msg.sessionUrl.split('?')[0])) {
                            matched = true;
                        } else if (myConvId && msg.sessionUrl.includes(myConvId)) {
                            matched = true;
                        } else if (cachedConvId && msg.sessionUrl.includes(cachedConvId)) {
                            matched = true;
                        }
                    }
                    sendResponse({ matched, taskId: myTaskId });
                    return true;
                }
            });

            chrome.runtime.onMessage.addListener(async (msg) => {
                if (msg && msg.type === 'WINDOW_SWITCHED') {
                    updateHUDStatus(`🎯 已切至任务 [${msg.taskId || ''}] 会话！`, '#10b981');
                }
                if (msg && msg.type === 'MEDIA_FOUND' && msg.items) {
                    let taskId = currentTask ? currentTask.id : getUrlTaskId();
                    if (!currentTask && taskId) {
                        currentTask = await fetchTaskContext(taskId);
                    }
                    if (!currentTask) {
                        const curConv = getCurrentConversationId();
                        if (curConv) {
                            currentTask = await fetchTaskContext('');
                            if (currentTask) taskId = currentTask.id;
                        }
                    }
                    if (!taskId || !currentTask) {
                        console.log('[VG Extension] 当前页面未绑定有效任务，忽略拦截到的媒体');
                        return;
                    }

                    // 1. 任务 ID 强校验：如果 ServiceWorker 消息中携带指派任务 targetTaskId，二者必须一致！
                    if (msg.targetTaskId && msg.targetTaskId !== taskId) {
                        console.log(`[VG Extension] 消息指派任务 [${msg.targetTaskId}] 与当前 Tab 任务 [${taskId}] 不匹配，安全忽略`);
                        return;
                    }

                    // 2. 会话 ID 强校验：
                    const pageConvId = getCurrentConversationId();
                    const msgConvId = msg.conversationId || '';
                    const cachedConvId = sessionStorage.getItem(`vg_conv_${taskId}`) || '';
                    const expectedConvId = pageConvId || cachedConvId;

                    if (pageConvId && taskId) {
                        sessionStorage.setItem(`vg_conv_${taskId}`, pageConvId);
                    }

                    if (msgConvId && expectedConvId && msgConvId !== expectedConvId) {
                        console.log(`[VG Extension] 消息会话ID [${msgConvId}] 与当前页面会话ID [${expectedConvId}] 不匹配，安全忽略其他Tab广播`);
                        return;
                    }

                    // 3. 时序铁律：发词物理耗时校验
                    const cachedSentTime = parseInt(sessionStorage.getItem(`vg_sent_time_${taskId}`) || '0', 10);
                    const sentTime = taskPromptSentTime || cachedSentTime;

                    if (sentTime) {
                        const now = Date.now();
                        const elapsed = now - sentTime;
                        // 豆包视频生成至少需要 20 秒以上！若低于 20 秒必为旧视频或其他Tab广播，坚决丢弃！
                        if (elapsed < 20000) {
                            console.log(`[VG Extension] 忽略拦截的媒体 (距发词仅 ${Math.round(elapsed / 1000)}s < 20s，成片物理未生成)`);
                            return;
                        }
                    } else {
                        // 未发词且未记录发词时间：仅在打捞模式且必须存在明确会话ID时允许
                        const isSalvageMode = currentTask && currentTask.sent && !currentTask.video_url;
                        if (!isSalvageMode || !expectedConvId) {
                            console.log('[VG Extension] 当前任务尚未发词且非匹配的打捞模式，忽略拦截到的媒体');
                            return;
                        }
                    }

                    const videoItems = msg.items.filter(it => it.type === 'video');
                    if (videoItems.length > 0) {
                        const targetVideo = videoItems[videoItems.length - 1];
                        console.log('[VG Extension] 成功捕获目标成片:', targetVideo.url);
                        await reportVideoReady(targetVideo.url);
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

                if (res && res.success && res.data && res.data.code === 0) {
                    if (res.data.downloaded) {
                        updateHUDStatus(`💾 该任务已在工作台下载完成，1秒后自动关闭标签页...`, '#10b981');
                        setTimeout(() => {
                            try { window.close(); } catch (e) {}
                            if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
                                chrome.runtime.sendMessage({ type: "CLOSE_CURRENT_TAB" });
                            }
                        }, 1000);
                        return;
                    }
                    const reportedTaskId = res.data?.task_id || taskId;
                    if (reportedTaskId === taskId) {
                        latestVideoUrl = videoUrl;
                        updateHUDStatus(`✅ 成片已绑定 [${reportedTaskId}]，可点击下载！`, '#10b981');
                        const dlBtn = document.getElementById('vg-btn-download');
                        if (dlBtn) dlBtn.style.display = 'inline-flex';
                    }
                    return;
                } else if (res && res.data && res.data.code !== 0) {
                    console.warn(`[VG Extension] 服务端拒绝绑定成片:`, res.data.message);
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
                if (data.downloaded) {
                    updateHUDStatus(`💾 该任务已在工作台下载完成，1秒后自动关闭标签页...`, '#10b981');
                    setTimeout(() => {
                        try { window.close(); } catch (e) {}
                        if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
                            chrome.runtime.sendMessage({ type: "CLOSE_CURRENT_TAB" });
                        }
                    }, 1000);
                    return;
                }
                const reportedTaskId = data.task_id || taskId;
                if (reportedTaskId === taskId) {
                    latestVideoUrl = videoUrl;
                    updateHUDStatus(`✅ 成片已绑定 [${reportedTaskId}]，可点击下载！`, '#10b981');
                    const dlBtn = document.getElementById('vg-btn-download');
                    if (dlBtn) dlBtn.style.display = 'inline-flex';
                }
            } else {
                console.warn('[VG Extension] 服务端拒绝绑定成片:', data.message);
            }
        } catch (e) {
            console.warn('[VG Extension] 上报视频直连失败:', e);
        }
    }

    // 顶层立即启动常驻成片监听（免疫 DOM 生命周期与页面重载）
    setupMessageListener();

    // 页面加载完成后立即执行
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // 运行在 Web 工作台 (127.0.0.1:8765) 上的消息网桥，实现下载完成即时广播关窗
    if (window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost') {
        window.addEventListener('message', (event) => {
            if (event.data && event.data.type === 'VG_TASK_DOWNLOADED' && event.data.taskId) {
                if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
                    chrome.runtime.sendMessage({
                        type: 'CLOSE_TASK_TAB_BY_ID',
                        taskId: event.data.taskId
                    }).catch(() => {});
                }
            }
        });
    }
})();
