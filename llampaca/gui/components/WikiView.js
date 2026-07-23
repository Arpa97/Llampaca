import { useWikiController } from '../controllers/wiki_controller.js';

// "Profilo" tab: a personal-memory manager over the wiki pages the model
// uses. Left: the list of pages. Right: a plain-markdown editor for the
// selected (or new) page. These are the very same files /remember writes and
// the model reads, so edits here are immediately visible to the assistant.
export default {
    template: `
        <div class="view">
            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Profilo</h1>
                    <div class="view-subtitle">Quello che l'assistente ricorda di te, come file markdown in <span class="mono">~/.llampaca/wiki/</span></div>
                </div>
                <div class="view-header-actions">
                    <button class="btn btn-primary" @click="newPage">
                        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/></svg>
                        Nuova pagina
                    </button>
                </div>
            </div>

            <div class="wiki-container">
                <!-- Pages list -->
                <div class="wiki-sidebar">
                    <div class="chat-sidebar-header">Pagine ({{ pages.length }})</div>
                    <div class="conversations-list">
                        <div v-if="!pages.length" style="padding: 14px; color: var(--text-faint); font-size: 12px; line-height: 1.55;">
                            Ancora niente in memoria. L'assistente crea una pagina quando gli chiedi di ricordare qualcosa — dal pulsante 🧠 in chat o con <code>/remember</code>.
                        </div>
                        <div v-for="p in pages" :key="p.name"
                             class="conversation-item" :class="{ active: selectedName === p.name }"
                             tabindex="0" role="button"
                             @click="selectPage(p.name)" @keyup.enter="selectPage(p.name)">
                            <div style="overflow: hidden;">
                                <div class="conversation-title mono">{{ p.name }}</div>
                                <div class="conversation-sub">{{ p.description }}</div>
                            </div>
                            <div class="conversation-delete" role="button" tabindex="0" title="Elimina pagina"
                                 @click.stop="deletePage(p.name)" @keyup.enter.stop="deletePage(p.name)">&times;</div>
                        </div>
                    </div>
                </div>

                <!-- Editor -->
                <div class="wiki-main">
                    <div v-if="selectedName === null && !isNew" class="wiki-empty">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg>
                        <h3>La memoria che condividi con l'assistente</h3>
                        <p>Scegli una pagina a sinistra per leggerla o correggerla, oppure creane una nuova. Sono file markdown veri: quello che scrivi qui l'assistente lo sa nelle prossime conversazioni.</p>
                    </div>

                    <div v-else class="wiki-editor">
                        <div class="form-group">
                            <label class="form-label">Nome pagina</label>
                            <input class="form-input" v-model="editName" :disabled="!isNew"
                                   style="font-family: var(--font-mono); font-size: 12.5px; max-width: 380px;"
                                   placeholder="es. preferenze-utente" />
                            <div class="form-help" v-if="isNew">Solo lettere e numeri; gli spazi diventano trattini. Il nome viene normalizzato al salvataggio.</div>
                            <div class="form-help" v-else>Il nome di una pagina esistente non si può cambiare: per rinominarla, creane una nuova.</div>
                        </div>

                        <div class="form-group" style="flex-grow: 1; display: flex; flex-direction: column; min-height: 0;">
                            <label class="form-label">Contenuto (markdown)</label>
                            <textarea class="form-input wiki-textarea" v-model="editContent"
                                      placeholder="Scrivi qui i fatti da ricordare…"></textarea>
                            <div class="wiki-editor-footer">
                                <span class="mono" :style="{ color: overLimit ? 'var(--danger)' : 'var(--text-faint)' }">
                                    {{ charCount }} / {{ maxChars }}
                                </span>
                                <button class="btn btn-primary" @click="savePage" :disabled="isSaving || overLimit">
                                    {{ isSaving ? 'Salvataggio…' : 'Salva pagina' }}
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
