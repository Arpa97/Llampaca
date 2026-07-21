import { useChatController } from '../controllers/chat_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
            <div class="view-header">
                <h1 class="view-title">Chat & Assistente</h1>
                <button class="btn btn-primary" @click="startNewConversation">Nuova Chat</button>
            </div>
            
            <div class="chat-container">
                <!-- Chat Sidebar (Scrolls independently) -->
                <div class="chat-sidebar">
                    <div class="chat-sidebar-header">Conversazioni</div>
                    <div class="conversations-list">
                        <div v-for="c in conversations" :key="c.id" 
                             class="conversation-item" :class="{ active: activeConversationId === c.id }"
                             @click="activeConversationId = c.id">
                            <div class="conversation-title">{{ c.title }}</div>
                            <div class="conversation-delete" @click.stop="deleteConversation(c.id)">&times;</div>
                        </div>
                    </div>
                </div>
                
                <!-- Messages Panel (Scrolls independently, input pinned at the bottom) -->
                <div class="chat-main" style="position: relative;"
                     @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
                    <div v-if="isDragging" class="drop-overlay">
                        <div class="drop-overlay-inner">📎 Rilascia i file per allegarli</div>
                    </div>
                    <div class="messages-list" ref="messagesContainer">
                        <div v-if="!getActiveMessages.length" style="margin: auto; text-align: center; color: var(--text-muted); max-width: 320px;">
                            <img src="logo.png" alt="Llampaca" style="width: 140px; border-radius: 12px; margin-bottom: 20px; opacity: 0.15;">
                            <h3>Come posso aiutarti oggi?</h3>
                            <p style="margin-top: 8px; font-size: 13px; line-height: 1.4;">Digita un messaggio per iniziare ad interagire con i tuoi tool MCP.</p>
                        </div>
                        <div v-for="(m, index) in getActiveMessages" :key="index" class="message-row" :class="m.role">
                            <div class="message-bubble markdown-body" v-html="parseMarkdown(m.content)" v-if="m.content"></div>
                            <div class="message-meta" v-if="m.thought" style="color: var(--amber-glow); font-style: italic; display: inline-flex; align-items: center; gap: 6px;">
                                <span class="spinner" style="width: 12px; height: 12px; border-width: 2px;"></span>
                                <span>{{ m.thought }}</span>
                            </div>
                            <div class="message-meta" v-else-if="m.timestamp">{{ m.timestamp }}</div>
                        </div>

                    </div>
                    
                    <!-- Sticky Prompt Confirmation Banner (Always visible above input bar) -->
                    <div v-if="pendingConfirmation" class="confirmation-box-sticky" style="margin: 10px 20px; padding: 16px; background: var(--bg-card); border: 2px solid var(--amber-glow); border-radius: 10px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); z-index: 100;">
                        <div class="confirmation-title" style="font-size: 14px; font-weight: 600; color: var(--amber-glow); margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
                            Autorizzazione Richiesta: <strong>{{ pendingConfirmation.name }}</strong>
                        </div>
                        <div class="confirmation-args" style="margin-bottom: 12px; max-height: 200px; overflow-y: auto; background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; padding: 10px;">
                            <pre style="margin: 0; font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all;">{{ formatArguments(pendingConfirmation.arguments) }}</pre>
                        </div>
                        <div class="confirmation-actions" style="display: flex; justify-content: flex-end; gap: 10px;">
                            <button class="btn btn-danger" @click="resolveConfirmation(false)" style="padding: 6px 14px; font-size: 12.5px;">Rifiuta Operazione</button>
                            <button class="btn btn-primary" @click="resolveConfirmation(true)" style="padding: 6px 16px; font-size: 12.5px; font-weight: 600;">Consenti ed Esegui</button>
                        </div>
                    </div>

                    <div class="chat-input-area">
                        <div v-if="contextBudget" style="font-size: 11px; color: var(--text-muted); margin-bottom: 8px; text-align: right; padding-right: 10px;">
                            Contesto: ~{{ contextBudget.used_percent }}% usato ({{ Math.round(contextBudget.used_tokens/1000 * 10)/10 }}k / {{ Math.round(contextBudget.total_tokens/1000 * 10)/10 }}k token)<template v-if="contextBudget.turn_seconds != null"> · {{ contextBudget.turn_seconds }}s</template><template v-if="contextBudget.tok_s != null"> · {{ contextBudget.gen_tokens }} tok @ {{ contextBudget.tok_s }} tok/s</template>
                        </div>
                        <div v-if="attachments.length" class="attachment-chips">
                            <div v-for="(a, i) in attachments" :key="i" class="attachment-chip" :class="a.status">
                                <span class="chip-icon">{{ a.status === 'uploading' ? '⏳' : (a.status === 'error' ? '⚠️' : (a.kind === 'rag' ? '🔍' : '📄')) }}</span>
                                <span class="chip-name" :title="a.name">{{ a.name }}</span>
                                <span v-if="a.detail" class="chip-detail">{{ a.detail }}</span>
                                <span class="chip-remove" @click="removeAttachment(i)" title="Rimuovi">&times;</span>
                            </div>
                        </div>
                        <div class="chat-input-box">
                            <input type="file" ref="fileInput" multiple @change="onFileChange" style="display: none;" />
                            <button class="attachment-btn" title="Allega un file" @click="openFilePicker">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                            </button>
                            <input class="chat-input-field" v-model="userInput" @keyup.enter="sendMessage" :disabled="isStreaming" placeholder="Digita una domanda o chiedi un'azione mail/tool..." />
                            <button v-if="!isStreaming" class="send-btn" @click="sendMessage" title="Invia">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                            </button>
                            <button v-else class="send-btn stop-btn" @click="stopGeneration" title="Interrompi la generazione">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const chatCtrl = useChatController();

        // Hidden <input type="file"> driven by the 📎 button. Kept here (not
        // in the controller) because it is a pure view concern — the DOM
        // element and the click that opens the native picker.
        const fileInput = Vue.ref(null);
        const openFilePicker = () => {
            if (fileInput.value) fileInput.value.click();
        };
        const onFileChange = (e) => {
            chatCtrl.attachFiles(e.target.files);
            // Reset so selecting the same file again re-fires @change.
            e.target.value = '';
        };

        // Collapse the attachment blocks that the backend merges into a user
        // message down to a compact "📎 filename" line, so a reloaded
        // conversation shows a tidy chip instead of the whole document text
        // (the model still received the full block; this is display-only).
        const collapseAttachments = (text) => {
            if (!text) return text;
            return text
                .replace(
                    /\[Attached file: (.+?) —[\s\S]*?\[End of attached file:[^\]]*\]/g,
                    (_m, name) => `📎 *${name.trim()}*`
                )
                .replace(
                    /\[Attached and indexed: (.+?) \([\s\S]*?\]/g,
                    (_m, name) => `📎 *${name.trim()}*`
                );
        };

        const parseMarkdown = (text) => {
            if (!text) return '';
            const clean = collapseAttachments(text);
            // Use marked if available, fallback to plain text replacing newlines
            if (window.marked) {
                return window.marked.parse(clean, { breaks: true });
            }
            return clean.replace(/\n/g, '<br>');
        };
        const formatArguments = (argsJson) => {
            try {
                const args = JSON.parse(argsJson);
                return Object.entries(args).map(([k, v]) => `${k}:\n  ${String(v).replace(/\n/g, '\n  ')}`).join('\n\n');
            } catch (e) {
                return argsJson;
            }
        };
        return {
            ...chatCtrl,
            fileInput,
            openFilePicker,
            onFileChange,
            parseMarkdown,
            formatArguments
        };
    }
};
