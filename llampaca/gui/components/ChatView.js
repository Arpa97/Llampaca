import { useChatController } from '../controllers/chat_controller.js';

export default {
    template: `
        <div class="view">
            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Chat</h1>
                    <div class="view-subtitle">Il tuo agente gira in locale: nessun dato lascia questa macchina.</div>
                </div>
                <div class="view-header-actions">
                    <button class="btn btn-primary" @click="startNewConversation">
                        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
                        Nuova chat
                    </button>
                </div>
            </div>

            <div class="chat-container">
                <!-- Elenco conversazioni (scorre in modo indipendente) -->
                <div class="chat-sidebar">
                    <div class="chat-sidebar-header">Conversazioni ({{ conversations.length }})</div>
                    <div class="conversations-list">
                        <div v-if="!conversations.length" style="padding: 14px; color: var(--text-faint); font-size: 12px; line-height: 1.5;">
                            Nessuna conversazione. Scrivi un messaggio: viene creata da sola.
                        </div>
                        <div v-for="c in conversations" :key="c.id"
                             class="conversation-item" :class="{ active: activeConversationId === c.id }"
                             tabindex="0" role="button"
                             @click="activeConversationId = c.id"
                             @keyup.enter="activeConversationId = c.id">
                            <div class="conversation-title">{{ c.title }}</div>
                            <div class="conversation-delete" role="button" tabindex="0" title="Elimina conversazione"
                                 @click.stop="deleteConversation(c.id)"
                                 @keyup.enter.stop="deleteConversation(c.id)">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Trascritto + composer -->
                <div class="chat-main"
                     @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
                    <div v-if="isDragging" class="drop-overlay">
                        <div class="drop-overlay-inner">Rilascia i file per allegarli</div>
                    </div>

                    <div class="messages-list" ref="messagesContainer">
                        <div v-if="!visibleMessages.length" class="chat-empty">
                            <img src="logo.png" alt="">
                            <h3>Come posso aiutarti?</h3>
                            <p>Chiedi qualcosa, allega un documento, oppure fai eseguire uno strumento. Ogni operazione che modifica file o esegue comandi ti chiede prima conferma.</p>
                        </div>

                        <div class="messages-inner" v-else>
                            <div v-for="(m, index) in visibleMessages" :key="index" class="message-row" :class="m.role">
                                <!-- Non "=== 'agent'": una conversazione ricaricata da
                                     SQLite marca il turno come 'assistant'. Con il
                                     confronto stretto l'etichetta compariva solo sui
                                     messaggi appena generati e spariva al ricaricamento. -->
                                <div v-if="m.role !== 'user'" class="message-role">Llampaca</div>
                                <div class="message-reasoning" v-if="m.reasoning">
                                    <details open>
                                        <summary class="reasoning-header">💡 Ragionamento del modello</summary>
                                        <div class="reasoning-content">{{ m.reasoning }}</div>
                                    </details>
                                </div>
                                <div class="message-bubble markdown-body" v-html="parseMarkdown(m.content)" v-if="m.content"></div>
                                <div class="message-thought" v-if="m.thought">
                                    <span class="thought-dot"></span>
                                    <span>{{ m.thought }}</span>
                                </div>
                                <div class="message-meta" v-else-if="m.timestamp">{{ m.timestamp }}</div>
                            </div>
                        </div>
                    </div>

                    <!-- Autorizzazione richiesta (coda FIFO). Sta sopra il composer
                         perché è bloccante: finché non rispondi, l'agente è fermo. -->
                    <div v-if="currentConfirmation" class="confirm-banner">
                        <div class="confirm-head">
                            <div class="confirm-title">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                                    <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                                </svg>
                                <span>Autorizzazione richiesta: <span class="tool-name">{{ currentConfirmation.name }}</span></span>
                            </div>
                            <span v-if="pendingConfirmations.length > 1" class="confirm-queue">
                                1 di {{ pendingConfirmations.length }}
                            </span>
                        </div>
                        <div class="confirmation-args">
                            <pre>{{ formatArguments(currentConfirmation.arguments) }}</pre>
                        </div>
                        <div class="confirmation-actions">
                            <button class="btn btn-danger" @click="resolveConfirmation(false)">Rifiuta</button>
                            <button class="btn btn-primary" @click="resolveConfirmation(true)">Consenti ed esegui</button>
                        </div>
                    </div>

                    <div class="chat-input-area">
                        <div class="chat-input-inner">
                            <!-- FIRMA: il misuratore di contesto. Si riempie d'ambra
                                 mentre la conversazione consuma la finestra di contesto
                                 e vira al crimson quando sta per esaurirsi. -->
                            <div v-if="contextBudget" class="ctx-meter" :class="ctxLevel">
                                <span class="ctx-label">Contesto</span>
                                <div class="ctx-track" role="progressbar" :aria-valuenow="contextBudget.used_percent"
                                     aria-valuemin="0" aria-valuemax="100"
                                     :aria-label="'Contesto usato: ' + contextBudget.used_percent + '%'">
                                    <div class="ctx-fill" :style="{ width: Math.min(100, contextBudget.used_percent) + '%' }"></div>
                                </div>
                                <span class="ctx-pct">{{ contextBudget.used_percent }}%</span>
                                <span class="ctx-stats">
                                    <span>{{ kTokens(contextBudget.used_tokens) }}/{{ kTokens(contextBudget.total_tokens) }} tok</span>
                                    <span v-if="contextBudget.turn_seconds != null">{{ contextBudget.turn_seconds }}s</span>
                                    <span v-if="contextBudget.tok_s != null">{{ contextBudget.tok_s }} tok/s</span>
                                </span>
                            </div>

                            <div v-if="attachments.length" class="attachment-chips">
                                <div v-for="(a, i) in attachments" :key="i" class="attachment-chip" :class="a.status">
                                    <span class="chip-icon">{{ a.status === 'uploading' ? '⏳' : (a.status === 'error' ? '⚠️' : (a.kind === 'image' ? '🖼️' : (a.kind === 'rag' ? '🔍' : '📄'))) }}</span>
                                    <span class="chip-name" :title="a.name">{{ a.name }}</span>
                                    <span v-if="a.detail" class="chip-detail">{{ a.detail }}</span>
                                    <span class="chip-remove" role="button" tabindex="0" @click="removeAttachment(i)"
                                          @keyup.enter="removeAttachment(i)" title="Rimuovi">&times;</span>
                                </div>
                            </div>

                            <div class="chat-input-box">
                                <input type="file" ref="fileInput" multiple @change="onFileChange" style="display: none;" />
                                <button class="icon-btn" title="Allega un file" @click="openFilePicker">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                                </button>
                                <!-- Flag "ricorda": è un INTERRUTTORE, non un invio immediato.
                                     Quando è armato (acceso in ambra) il prossimo messaggio
                                     viene salvato come memoria permanente nel Profilo. -->
                                <button class="icon-btn" :class="{ active: rememberMode }"
                                        :title="rememberMode ? 'Memoria attiva: il prossimo messaggio viene salvato nel Profilo. Clicca per disattivare.' : 'Salva il prossimo messaggio nella memoria permanente'"
                                        @click="toggleRemember">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/></svg>
                                </button>
                                <!-- Deep Search Flag -->
                                <button class="icon-btn" :class="{ active: deepSearchMode }"
                                        :title="deepSearchMode ? 'Deep Search attiva: il modello cercherà nel web prima di rispondere. Clicca per disattivare.' : 'Forza il modello a fare una ricerca web approfondita per il prossimo messaggio'"
                                        @click="toggleDeepSearch">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                                        <circle cx="11" cy="11" r="8"></circle>
                                        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                                        <line x1="11" y1="8" x2="11" y2="14"></line>
                                        <line x1="8" y1="11" x2="14" y2="11"></line>
                                    </svg>
                                </button>
                                <!-- Risposte dirette: salta il ragionamento del
                                     modello. È una scelta per turno, non una
                                     configurazione, quindi sta qui accanto al
                                     campo e non sepolta in Impostazioni. -->
                                <button class="icon-btn" :class="{ active: directMode }"
                                        :title="directMode ? 'Risposte dirette ATTIVE: il modello risponde subito, senza ragionare. Clicca per farlo ragionare (più lento, meglio sui compiti in più passaggi).' : 'Il modello ragiona prima di rispondere: più lento. Clicca per ottenere risposte dirette.'"
                                        @click="toggleDirect">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                                </button>
                                <textarea class="chat-input-field" ref="inputField" rows="1"
                                          v-model="userInput" :disabled="isStreaming"
                                          @input="autoGrow" @keydown.enter.exact.prevent="submit"
                                          :placeholder="rememberMode ? 'Memoria attiva: scrivi cosa ricordare e invia…' : 'Scrivi un messaggio…'"></textarea>
                                <button v-if="!isStreaming" class="icon-btn send-btn" @click="submit" title="Invia">
                                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                                </button>
                                <button v-else class="icon-btn stop-btn" @click="stopGeneration" title="Interrompi la generazione">
                                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
                                </button>
                            </div>

                            <div class="composer-hint">
                                <kbd>Invio</kbd> invia · <kbd>Maiusc</kbd>+<kbd>Invio</kbd> va a capo
                            </div>
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

        // --- Composer multiriga ------------------------------------------
        // Il campo era un <input> a riga singola: un prompt di tre frasi
        // scorreva via orizzontalmente. Ora è un <textarea> che cresce con il
        // testo fino a un tetto (max-height nel CSS, poi scorre).
        const inputField = Vue.ref(null);
        const autoGrow = () => {
            const el = inputField.value;
            if (!el) return;
            el.style.height = 'auto';
            el.style.height = `${el.scrollHeight}px`;
        };
        // Invia e riporta il campo a una riga sola.
        const submit = async () => {
            await chatCtrl.sendMessage();
            Vue.nextTick(() => {
                if (inputField.value) inputField.value.style.height = 'auto';
            });
        };

        // Il prompt di sistema non è una battuta della conversazione: è
        // configurazione. Una sessione avviata dalla CLI lo salva come
        // messaggio con role 'system', e il trascritto lo mostrava in cima
        // come se l'assistente avesse detto "You are Llampaca, a helpful
        // local AI personal assistant." Il backend lo salta già quando
        // ricostruisce il contesto per il modello (server.py); qui facciamo
        // lo stesso per la visualizzazione.
        const visibleMessages = Vue.computed(() =>
            chatCtrl.getActiveMessages.value.filter(m => m.role !== 'system')
        );

        // --- Misuratore di contesto ---------------------------------------
        // Soglie: sotto il 75% l'ambra racconta solo l'occupazione; oltre il
        // 90% il colore diventa un avviso, perché da lì in poi la conversazione
        // inizia a perdere i messaggi più vecchi.
        const ctxLevel = Vue.computed(() => {
            const b = chatCtrl.contextBudget.value;
            if (!b) return '';
            if (b.used_percent >= 90) return 'crit';
            if (b.used_percent >= 75) return 'warn';
            return '';
        });

        // 2340 → "2.3k". Tiene la riga del misuratore di larghezza stabile.
        const kTokens = (n) => {
            if (n == null) return '—';
            return n >= 1000 ? `${Math.round(n / 100) / 10}k` : String(n);
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

        // Collapse a "/remember" turn down to a clean "🧠 <fact>" line. The
        // backend rewrites a remembered message into "<fact>\n\n[The user asked
        // to remember the fact above permanently. …]" before persisting it, so
        // a reloaded conversation would otherwise show that whole instruction
        // block. Here we strip it and keep just the fact with the brain emoji,
        // matching what the live bubble shows. Display-only: the model still
        // received the full instruction.
        const collapseRemember = (text) => {
            if (!text) return text;
            const m = text.match(/^([\s\S]*?)\s*\[The user asked to remember the fact above permanently\.[\s\S]*\]\s*$/);
            if (m) return `🧠 ${m[1].trim()}`;
            return text;
        };

        const collapseDeepSearch = (text) => {
            if (!text) return text;
            const m = text.match(/^([\s\S]*?)\s*\[The user requested a Deep Search\.[\s\S]*\]\s*$/);
            if (m) return `🌐 ${m[1].trim()}`;
            return text;
        };

        const formatImageLinks = (text) => {
            if (!text) return text;
            if (text.includes('![') && text.includes('/api/media?path=')) return text;
            return text.replace(
                /(?:!\[([^\]]*)\]\((?:file:\/\/|\/api\/media\?path=)?([^)\s]+)\)|\[([^\]]*)\]\((?:file:\/\/)?([^)\s]+\.(?:png|jpg|jpeg|webp|gif))\)|(?:file:\/\/|~|\/Users|\/home|[a-zA-Z]:)[^\s)]+?\.(?:png|jpg|jpeg|webp|gif))/gi,
                (match, alt1, path1, alt2, path2) => {
                    let path = path1 || path2 || match;
                    if (path.startsWith('file://')) path = path.slice(7);
                    if (!/\.(png|jpg|jpeg|webp|gif)$/i.test(path)) return match;
                    const cleanPath = decodeURIComponent(path);
                    const mediaUrl = '/api/media?path=' + encodeURIComponent(cleanPath);
                    const altText = alt1 || alt2 || 'Immagine generata';
                    return `\n\n![${altText}](${mediaUrl})\n\n[📁 Apri file originale](file://${cleanPath})`;
                }
            );
        };

        const parseMarkdown = (rawText) => {
            if (!rawText) return '';
            let text = rawText;
            if (Array.isArray(rawText)) {
                // Multimodal message: extract text parts and add image placeholders
                text = rawText.map(item => {
                    if (item.type === 'text') return item.text;
                    if (item.type === 'image_url') return `📎 *[Immagine allegata]*`;
                    return '';
                }).join('\n\n');
            }
            const clean = formatImageLinks(collapseDeepSearch(collapseRemember(collapseAttachments(text))));
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
            inputField,
            autoGrow,
            submit,
            visibleMessages,
            ctxLevel,
            kTokens,
            parseMarkdown,
            formatArguments
        };
    }
};
