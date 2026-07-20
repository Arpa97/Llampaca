import { useWikiController } from '../controllers/wiki_controller.js';

// "Profilo" tab: a personal-memory manager over the wiki pages the model
// uses. Left: the list of pages. Right: a plain-markdown editor for the
// selected (or new) page. These are the very same files /remember writes and
// the model reads, so edits here are immediately visible to the assistant.
export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%;">
            <div class="view-header">
                <h1 class="view-title">Profilo — Memoria personale</h1>
                <button class="btn btn-primary" @click="newPage">Nuova pagina</button>
            </div>

            <div class="wiki-container">
                <!-- Pages list -->
                <div class="wiki-sidebar">
                    <div class="chat-sidebar-header">Pagine ({{ pages.length }})</div>
                    <div class="conversations-list">
                        <div v-if="!pages.length" style="padding: 16px; color: var(--text-muted); font-size: 13px; line-height: 1.5;">
                            Nessuna memoria salvata. L'assistente crea pagine quando gli chiedi di ricordare qualcosa (o con <code>/remember</code> in chat); puoi anche crearle qui.
                        </div>
                        <div v-for="p in pages" :key="p.name"
                             class="conversation-item" :class="{ active: selectedName === p.name }"
                             @click="selectPage(p.name)">
                            <div style="overflow: hidden;">
                                <div class="conversation-title">{{ p.name }}</div>
                                <div style="font-size: 11px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">{{ p.description }}</div>
                            </div>
                            <div class="conversation-delete" @click.stop="deletePage(p.name)">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Editor -->
                <div class="wiki-main">
                    <div v-if="selectedName === null && !isNew" class="wiki-empty">
                        <h3>La tua memoria condivisa con l'assistente</h3>
                        <p>Seleziona una pagina a sinistra per leggerla o modificarla, oppure crea una <strong>Nuova pagina</strong>. Sono file markdown veri in <code>~/.llampaca/wiki/</code>: quello che scrivi qui l'assistente lo sa nelle prossime conversazioni.</p>
                    </div>

                    <div v-else class="wiki-editor">
                        <div class="form-group">
                            <label class="form-label">Nome pagina</label>
                            <input class="form-input" v-model="editName" :disabled="!isNew"
                                   placeholder="es. preferenze-utente" />
                            <div class="form-help" v-if="isNew">Solo lettere e numeri; gli spazi diventano trattini. Salvando, il nome viene normalizzato.</div>
                            <div class="form-help" v-else>Il nome di una pagina esistente non è modificabile (crea una nuova pagina per rinominare).</div>
                        </div>

                        <div class="form-group" style="flex-grow: 1; display: flex; flex-direction: column;">
                            <label class="form-label">Contenuto (markdown)</label>
                            <textarea class="form-input wiki-textarea" v-model="editContent"
                                      placeholder="Scrivi qui i fatti da ricordare (markdown)..."></textarea>
                            <div class="wiki-editor-footer">
                                <span :style="{ color: overLimit ? 'var(--danger, #b91c1c)' : 'var(--text-muted)' }">
                                    {{ charCount }} / {{ maxChars }} caratteri
                                </span>
                                <button class="btn btn-primary" @click="savePage" :disabled="isSaving || overLimit">
                                    {{ isSaving ? 'Salvataggio...' : 'Salva pagina' }}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        return useWikiController();
    }
};
