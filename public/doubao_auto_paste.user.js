// ==UserScript==
// @name         VG 豆包生产一键填词与参考图自动导入助手
// @namespace    http://tampermonkey.net/
// @version      2.2
// @description  与本地 VG Web 工作台全自动联动：按任务自动获取提示词和参考图，自动精准填入豆包 ProseMirror 输入框，自动粘贴参考图，自动同步今日额度
// @author       VG Assistant
// @match        *://*.doubao.com/*
// @match        *://doubao.com/*
// @include      *://*.doubao.com/*
// @include      *://doubao.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-end
// @noframes
// @updateURL    http://127.0.0.1:8765/doubao_auto_paste.user.js
// @downloadURL  http://127.0.0.1:8765/doubao_auto_paste.user.js
// ==/UserScript==

(function() {
    'use strict';

    const API_BASE = 'http://127.0.0.1:8765';
    let currentTask = null;
    let autoInjected = false;
    let importInProgress = false;

    console.log('[VG Assistant 2.1] 豆包全自动生产助手启动');

    // 解析当前 URL 或 Hash 中的任务 ID (例如 ?vg_task=V01 或 #vg_task=V01)
    function getUrlTaskId() {
        try {
            // 1. 查询参数
            const params = new URLSearchParams(window.location.search);
            if (params.get('vg_task')) return params.get('vg_task');

            // 2. Hash 参数
            const hash = window.location.hash.replace(/^#/, '');
            const hashParams = new URLSearchParams(hash);
            if (hashParams.get('vg_task')) return hashParams.get('vg_task');

            // 3. 兜底正则
            const m = window.location.href.match(/vg_task=([A-Za-z0-9_-]+)/);
            if (m) return m[1];
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

        return new Promise((resolve) => {
            const handleSuccess = (resText) => {
                try {
                    const data = JSON.parse(resText);
                    if (data.code === 0 && data.data) {
                        resolve(data.data);
                    } else {
                        resolve(null);
                    }
                } catch (e) {
                    resolve(null);
                }
            };

            if (typeof GM_xmlhttpRequest !== 'undefined') {
                GM_xmlhttpRequest({
                    method: 'GET',
                    url: url,
                    onload: function(res) {
                        handleSuccess(res.responseText);
                    },
                    onerror: function() {
                        // 降级尝试原生 fetch
                        fetch(url)
                            .then(r => r.json())
                            .then(data => resolve(data.code === 0 ? data.data : null))
                            .catch(() => resolve(null));
                    }
                });
            } else {
                fetch(url)
                    .then(r => r.json())
                    .then(data => resolve(data.code === 0 ? data.data : null))
                    .catch(() => resolve(null));
            }
        });
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
            console.error('[VG Assistant] Base64 转 File 失败:', e);
            return null;
        }
    }

    // 将图片 URL 转为 File 对象 (支持 Base64 与跨域 Blob)
    async function fetchImageFile(imgObj) {
        if (imgObj.base64) {
            const f = b64ToFile(imgObj.base64, imgObj.name || imgObj.filename);
            if (f) return f;
        }

        return new Promise((resolve, reject) => {
            const handleBlob = (blob) => {
                const file = new File([blob], imgObj.filename || imgObj.name || 'reference.png', {
                    type: blob.type || 'image/png'
                });
                resolve(file);
            };

            if (typeof GM_xmlhttpRequest !== 'undefined') {
                GM_xmlhttpRequest({
                    method: 'GET',
                    url: imgObj.url,
                    responseType: 'blob',
                    onload: function(res) {
                        if (res.response) {
                            handleBlob(res.response);
                        } else {
                            reject(new Error('No response blob'));
                        }
                    },
                    onerror: reject
                });
            } else {
                fetch(imgObj.url)
                    .then(r => r.blob())
                    .then(handleBlob)
                    .catch(reject);
            }
        });
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
            // 1. 设置选区到末尾或全选
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
                console.log('[VG Assistant] Paste 事件成功注入文本');
                return true;
            }
        } catch (e) {
            console.warn('[VG Assistant] Paste 注入报错:', e);
        }

        // 备用方案 1: beforeinput
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

    // 自动上传参考图片 (通过 Paste 派发给 ProseMirror)
    async function uploadReferenceImages(editorEl, images) {
        if (!images || images.length === 0 || !editorEl) return 0;
        
        // 优先选取提示词中明确命中的所有图片（最多10张）；若未提及则兜底取前 4 张主要参考图
        const matchedImgs = images.filter(img => img.matched);
        const targetImgs = matchedImgs.length > 0 ? matchedImgs.slice(0, 10) : images.slice(0, 4);

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
                console.log('[VG Assistant] 成功粘贴参考图:', img.name);
                // 间隔 400ms，防止并发冲突
                await new Promise(r => setTimeout(r, 450));
            } catch (err) {
                console.warn('[VG Assistant] 上传参考图异常:', img.name, err);
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

        const url = `${API_BASE}/api/task/mark_step`;
        const bodyStr = JSON.stringify(payload);

        if (typeof GM_xmlhttpRequest !== 'undefined') {
            GM_xmlhttpRequest({
                method: 'POST',
                url: url,
                headers: { 'Content-Type': 'application/json' },
                data: bodyStr,
                onload: () => console.log('[VG Assistant] 工作台已标记已发送 -1条额度')
            });
        } else {
            fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: bodyStr
            }).catch(() => {});
        }
    }

    // 执行全自动导入主逻辑
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
            const textOk = injectPromptText(inputEl, currentTask.prompt);

            // 无论文本注入如何，自动将提示词放入剪贴板保底
            if (currentTask.prompt) {
                if (typeof GM_setClipboard !== 'undefined') {
                    GM_setClipboard(currentTask.prompt);
                } else if (navigator.clipboard) {
                    navigator.clipboard.writeText(currentTask.prompt).catch(() => {});
                }
            }

            // 等待 500ms 让 ProseMirror 完成文本状态更新，再注入图片
            if (currentTask.ref_images && currentTask.ref_images.length > 0) {
                updateHUDStatus('🖼️ 正在导入参考图...', '#3b82f6');
                await new Promise(r => setTimeout(r, 500));
                await uploadReferenceImages(inputEl, currentTask.ref_images);
            }

            updateHUDStatus('✅ 词与图已自动导入就绪！', '#10b981');
            await markTaskSent(currentTask);
            autoInjected = true;
        } catch (e) {
            console.error('[VG Assistant] 导入失败:', e);
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

    // 创建右下角悬浮控制台 HUD
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
            width: 320px;
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
                    <div style="display: flex; gap: 6px; overflow-x: auto;">
                        ${displayImgs.map((img, i) => `
                            <div style="background: #232336; border: 1px solid #3b4261; border-radius: 6px; padding: 4px; text-align: center; cursor: pointer; flex-shrink: 0;" onclick="window._vgCopyImg(${i})" title="点击直接复制图片: ${img.name}">
                                <img src="${img.base64 || img.url}" style="width: 52px; height: 52px; object-fit: cover; border-radius: 4px; display: block;" />
                                <div style="font-size: 10px; color: #cbd5e1; margin-top: 2px; max-width: 52px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${img.name}</div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        hud.innerHTML = `
            <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 10px 14px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; cursor: pointer;" id="vg-hud-header">
                <span style="display: flex; align-items: center; gap: 6px;">
                    ⚡ VG 自动化助手 ${task ? `<span style="background: #fbbf24; color: #000; font-size: 11px; padding: 1px 6px; border-radius: 4px;">${task.id}</span>` : ''}
                </span>
                <span id="vg-hud-toggle" style="font-size: 14px;">▼</span>
            </div>
            <div id="vg-hud-content" style="padding: 12px 14px;">
                <div style="font-weight: 600; color: #f8fafc; font-size: 13px; line-height: 1.4; margin-bottom: 4px;">
                    ${task ? task.title : '未绑定任务 (点击刷新)'}
                </div>
                <div id="vg-hud-status" style="font-size: 12px; color: #60a5fa; margin-bottom: 8px;">
                    🟢 已就绪 (等待输入框)
                </div>
                ${refImgsHtml}
                <div style="margin-top: 10px; display: flex; gap: 6px;">
                    <button id="vg-btn-import" style="flex: 1; background: #2563eb; color: #fff; border: none; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; box-shadow: 0 2px 8px rgba(37,99,235,0.4);">
                        🚀 重新自动导入 (词+图)
                    </button>
                    <button id="vg-btn-copy" style="background: #334155; color: #cbd5e1; border: none; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 12px;">
                        📋 拷词
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(hud);

        // 挂载全局图片复制方法
        window._vgCopyImg = (index) => {
            if (task && task.ref_images && task.ref_images[index]) {
                copyImageToClipboard(task.ref_images[index]);
            }
        };

        // 按钮事件
        document.getElementById('vg-btn-import').addEventListener('click', () => executeAutoImport(false));
        document.getElementById('vg-btn-copy').addEventListener('click', async () => {
            if (task && task.prompt) {
                if (typeof GM_setClipboard !== 'undefined') {
                    GM_setClipboard(task.prompt);
                } else if (navigator.clipboard) {
                    await navigator.clipboard.writeText(task.prompt);
                }
                updateHUDStatus('提示词已复制到剪贴板！可直接 Ctrl+V', '#10b981');
            }
        });

        // 折叠展开
        let isCollapsed = false;
        document.getElementById('vg-hud-header').addEventListener('click', (e) => {
            if (e.target.id === 'vg-btn-import' || e.target.id === 'vg-btn-copy') return;
            isCollapsed = !isCollapsed;
            document.getElementById('vg-hud-content').style.display = isCollapsed ? 'none' : 'block';
            document.getElementById('vg-hud-toggle').innerText = isCollapsed ? '▲' : '▼';
        });
    }

    // 初始化轮询与自动执行
    async function init() {
        const taskId = getUrlTaskId();
        currentTask = await fetchTaskContext(taskId);
        
        if (currentTask) {
            console.log('[VG Assistant] 成功加载任务数据:', currentTask.id, currentTask.title);
            renderHUD(currentTask);

            // 轮询等待豆包输入框渲染，就绪后延时自动执行填入与传图
            let attempts = 0;
            const timer = setInterval(async () => {
                attempts++;
                const inputEl = findChatInput();
                if (inputEl && !autoInjected) {
                    clearInterval(timer);
                    updateHUDStatus('🎯 输入框已就绪，正在自动导入...');
                    setTimeout(() => {
                        executeAutoImport(true);
                    }, 1000);
                }
                if (attempts > 40) {
                    clearInterval(timer);
                    updateHUDStatus('等待超时，请点击下方按钮手动导入');
                }
            }, 500);
        } else {
            console.log('[VG Assistant] 未获取到本地流水线任务，渲染备用面板');
            renderHUD(null);
        }

        // 注册油猴专属弹出菜单命令
        if (typeof GM_registerMenuCommand !== 'undefined') {
            GM_registerMenuCommand("⚡ 立即自动导入词和图", () => {
                executeAutoImport(false);
            });
            GM_registerMenuCommand("📋 复制当前任务提示词", async () => {
                if (currentTask && currentTask.prompt) {
                    if (typeof GM_setClipboard !== 'undefined') {
                        GM_setClipboard(currentTask.prompt);
                    } else if (navigator.clipboard) {
                        await navigator.clipboard.writeText(currentTask.prompt);
                    }
                    alert('已复制提示词！可以直接在输入框按 Ctrl+V 粘贴');
                } else {
                    alert('当前未加载到任务');
                }
            });
        }

        // 监听解密无水印视频消息或全局事件
        window.addEventListener('message', async (e) => {
            if (e.data && e.data.type === 'MEDIA_FOUND' && e.data.items) {
                const videoItems = e.data.items.filter(it => it.type === 'video');
                for (const v of videoItems) {
                    await reportVideoReady(v.url);
                }
            }
        });
    }

    // 上报无水印成片链接到 Web 工作台
    async function reportVideoReady(videoUrl) {
        const taskId = currentTask ? currentTask.id : getUrlTaskId();
        updateHUDStatus('🎉 已捕获无水印成片！正在同步工作台...');
        try {
            const resp = await fetch(`${API_BASE}/api/task/report_video`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    task_id: taskId,
                    video_url: videoUrl,
                    account_id: (currentTask && currentTask.account) ? currentTask.account : ''
                })
            });
            const data = await resp.json();
            if (data.code === 0) {
                updateHUDStatus(`✅ 无水印视频已成功绑定任务 [${data.task_id || taskId || '已分配'}]！`);
            }
        } catch (e) {
            console.warn('[VG Extension] 上报视频失败:', e);
        }
    }

    // 立即启动
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
