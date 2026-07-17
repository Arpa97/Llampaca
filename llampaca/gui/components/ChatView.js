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
                <div class="chat-main">
                    <div class="messages-list" ref="messagesContainer">
                        <div v-if="!getActiveMessages.length" style="margin: auto; text-align: center; color: var(--text-muted); max-width: 320px;">
                            <img src="logo.png" alt="Llampaca" style="width: 140px; border-radius: 12px; margin-bottom: 20px; opacity: 0.15;">
                            <h3>Come posso aiutarti oggi?</h3>
                            <p style="margin-top: 8px; font-size: 13px; line-height: 1.4;">Digita un messaggio per iniziare ad interagire con i tuoi tool MCP.</p>
                        </div>
                        <div v-for="(m, index) in getActiveMessages" :key="index" class="message-row" :class="m.role">
                            <div class="message-bubble markdown-body" v-html="parseMarkdown(m.content)" v-if="m.content"></div>
                            <div class="message-meta" v-if="m.thought" style="color: var(--amber-glow); font-style: italic;">
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: -2px; margin-right: 2px;"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
                                {{ m.thought }}
                            </div>
                            <div class="message-meta" v-else-if="m.timestamp">{{ m.timestamp }}</div>
                        </div>

                        <div v-if="pendingConfirmation" class="confirmation-box">
                            <div class="confirmation-title">🛠️ L'agente vuole eseguire: <strong>{{ pendingConfirmation.name }}</strong></div>
                            <div class="confirmation-args">
                                <pre>{{ formatArguments(pendingConfirmation.arguments) }}</pre>
                            </div>
                            <div class="confirmation-actions">
                                <button class="btn btn-danger" @click="resolveConfirmation(false)">Rifiuta</button>
                                <button class="btn btn-primary" @click="resolveConfirmation(true)">Consenti</button>
                            </div>
                        </div>
                    </div>
                    
                    <div class="chat-input-area">
                        <div v-if="contextBudget" style="font-size: 11px; color: var(--text-muted); margin-bottom: 8px; text-align: right; padding-right: 10px;">
                            Contesto occupato: {{ Math.round((contextBudget.used_chars / contextBudget.max_chars) * 100) }}% ({{ Math.round(contextBudget.used_chars/1000) }}k / {{ Math.round(contextBudget.max_chars/1000) }}k char)
                        </div>
                        <div class="chat-input-box">
                            <button class="attachment-btn" title="Allega un file">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                            </button>
                            <input class="chat-input-field" v-model="userInput" @keyup.enter="sendMessage" placeholder="Digita una domanda o chiedi un'azione mail/tool..." />
                            <button class="send-btn" @click="sendMessage">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const chatCtrl = useChatController();
        const parseMarkdown = (text) => {
            if (!text) return '';
            // Use marked if available, fallback to plain text replacing newlines
            if (window.marked) {
                return window.marked.parse(text, { breaks: true });
            }
            return text.replace(/\n/g, '<br>');
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
            parseMarkdown,
            formatArguments
        };
    }
};
