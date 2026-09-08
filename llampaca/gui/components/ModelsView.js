import { useModelsController } from '../controllers/models_controller.js';

// A single catalog card. Extracted so the LLM and embedding sections can each
// render the same card markup without duplication. It stays presentational:
// all actions are emitted up to the parent (which owns the controller).
const ModelCard = {
    props: { m: { type: Object, required: true } },
    emits: ['set-default', 'delete', 'download'],
    // "Imposta default" wording depends on the kind so the user knows whether
    // they are choosing the chat model or the embedding model.
    computed: {
        defaultLabel() {
            if (this.m.kind === 'embedding') return 'Usa per embedding';
            if (this.m.kind === 'image') return 'Usa per immagini';
            return 'Usa come default';
        }
    },
    template: `
        <div class="model-card" :class="{ active: m.active, downloading: m.downloading }">
            <div class="model-card-header">
                <div class="model-card-title">{{ m.preset_id || m.name }}</div>
                <div v-if="m.active" class="model-badge success">Attivo</div>
                <div v-else-if="m.installed" class="model-badge secondary">Installato</div>
                <div v-else-if="m.downloading" class="model-badge warning">Download</div>
                <div v-else class="model-badge info">Disponibile</div>
            </div>

            <!-- Il nome file GGUF è un dato macchina: monospazio, non titolo. -->
            <div v-if="m.preset_id" class="model-card-file">{{ m.name }}</div>

            <div class="model-card-desc">{{ m.description }}</div>
            
            <div v-if="m.kind === 'projector' && m.linked_models && m.linked_models.length > 0" style="margin: 8px 0; font-size: 11px; padding: 6px; border-radius: var(--radius-sm); background: var(--bg-100);">
                <div style="color: var(--text-muted); margin-bottom: 2px;">Collegato a:</div>
                <div v-for="l in m.linked_models" :key="l" style="font-family: var(--font-mono); color: var(--pacific-500);">{{ l }}</div>
            </div>

            <div class="model-card-meta">
                <span>Dimensione <strong>{{ m.size }}</strong></span>
                <span>Quant. <strong>{{ m.quant }}</strong></span>
            </div>

            <!-- Progress Bar for active downloads -->
            <div v-if="m.downloading">
                <div style="display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 6px; color: var(--text-muted);">
                    <span>Scaricamento</span>
                    <strong style="font-family: var(--font-mono); color: var(--amber-deep);">{{ m.progress }}%</strong>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" :style="{ width: m.progress + '%' }"></div>
                </div>
            </div>

            <!-- Action Buttons -->
            <div class="model-card-actions">
                <!-- Case 1: Installed -->
                <template v-if="m.installed">
                    <button v-if="!m.active && m.kind !== 'projector'" class="btn btn-secondary" @click="$emit('set-default', m)">{{ defaultLabel }}</button>
                    <button class="btn btn-danger" @click="$emit('delete', m.id)">Elimina</button>
                </template>
                <!-- Case 2: Downloading -->
                <template v-else-if="m.downloading">
                    <button class="btn btn-secondary btn-block" disabled>Scaricamento in corso…</button>
                </template>
                <!-- Case 3: Available to download -->
                <template v-else>
                    <button class="btn btn-pacific btn-block" @click="$emit('download', m)">Scarica</button>
                </template>
            </div>
        </div>
    `
};

export default {
    components: { ModelCard },
    template: `
        <div class="view">
            <!-- Loading Overlay -->
            <div v-if="isRestarting" class="loading-overlay">
                <div class="spinner"></div>
                <div>Riavvio del server dei modelli…</div>
            </div>

            <div class="view-header">
                <div class="view-header-text">
                    <h1 class="view-title">Modelli</h1>
                    <div class="view-subtitle">I file GGUF scaricati su questa macchina, in <span class="mono">~/.llampaca/models/</span></div>
                </div>
            </div>

            <div class="view-body view-body-pad">
                <div class="view-stack">
                    <!-- Welcome Banner when no chat models are installed -->
                    <div v-if="!hasInstalledChatModels" style="background: rgba(255, 166, 48, 0.12); border: 1px solid var(--amber-glow); border-radius: var(--radius-md); padding: 16px; margin-bottom: 24px; color: var(--dusk-blue);">
                        <div style="font-weight: 700; font-size: 15px; margin-bottom: 4px;">👋 Benvenuto in Llampaca!</div>
                        <div style="font-size: 13px; opacity: 0.9;">Nessun modello di chat risulta installato. Scegli e scarica uno dei modelli consigliati qui sotto per iniziare a conversare col tuo agente locale.</div>
                    </div>

                    <!-- LLM (chat) models: the model that answers in chat. -->
                    <div class="section-head">
                        <h3 class="section-title">Modelli di chat</h3>
                        <p class="section-desc">Generano le risposte in conversazione. Quello impostato come default è il modello con cui parli.</p>
                    </div>
                    <div class="models-grid">
                        <model-card
                            v-for="m in chatModels"
                            :key="m.id"
                            :m="m"
                            @set-default="onSetDefault"
                            @delete="deleteModel"
                            @download="onDownload"
                        />
                    </div>

                    <!-- Embedding models: used ONLY for document search / RAG, never
                         to chat. Selecting one here sets the embedding default, not
                         the chat default. -->
                    <div class="section-head">
                        <h3 class="section-title">Modelli di embedding</h3>
                        <p class="section-desc">Servono alla ricerca nei documenti allegati (RAG): indicizzano e cercano, non rispondono. Il default qui è indipendente dal modello di chat.</p>
                    </div>
                    <div v-if="embeddingModels.length === 0" class="empty-panel">
                        Nessun modello di embedding nel catalogo.
                    </div>
                    <div v-else class="models-grid">
                        <model-card
                            v-for="m in embeddingModels"
                            :key="m.id"
                            :m="m"
                            @set-default="onSetDefault"
                            @delete="deleteModel"
                            @download="onDownload"
                        />
                    </div>

                    <!-- Image Generation models -->
                    <div class="section-head">
                        <h3 class="section-title">Generazione Immagini</h3>
                        <p class="section-desc">Modelli Stable Diffusion GGUF per la generazione di immagini in locale. Vengono caricati in memoria solo quando richiesti (on-demand).</p>
                    </div>
                    <div v-if="imageModels.length === 0" class="empty-panel">
                        Nessun modello di generazione immagini nel catalogo.
                    </div>
                    <div v-else class="models-grid">
                        <model-card
                            v-for="m in imageModels"
                            :key="m.id"
                            :m="m"
                            @set-default="onSetDefault"
                            @delete="deleteModel"
                            @download="onDownload"
                        />
                    </div>

                    <!-- Projector models -->
                    <div class="section-head">
                        <h3 class="section-title">Proiettori Multimodali (Vision)</h3>
                        <p class="section-desc">File GGUF accessori che permettono al modello chat principale di "vedere" le immagini. Vengono avviati in automatico col modello a cui sono collegati e cancellati assieme ad esso.</p>
                    </div>
                    <div v-if="projectorModels.length === 0" class="empty-panel">
                        Nessun proiettore vision installato.
                    </div>
                    <div v-else class="models-grid">
                        <model-card
                            v-for="m in projectorModels"
                            :key="m.id"
                            :m="m"
                            @set-default="onSetDefault"
                            @delete="deleteModel"
                            @download="onDownload"
                        />
                    </div>

                    <!-- Hugging Face Search Browser Results -->
                    <div class="section-head">
                        <h3 class="section-title">
                            Esplora Hugging Face
                            <span v-if="isSearching" class="spinner spinner-sm"></span>
                        </h3>
                        <p class="section-desc">Cerca altri modelli GGUF da scaricare, oppure incolla direttamente un percorso o un URL.</p>
                    </div>

                    <!-- Download & Search Panel -->
                    <div class="download-panel" style="display: flex; gap: 22px; flex-wrap: wrap;">
                        <div style="flex: 1.2; min-width: 300px;">
                            <h3 class="download-panel-title">Cerca un repository</h3>
                            <div class="download-input-group">
                                <input class="download-input" v-model="searchQuery" placeholder="es. llama, deepseek, gemma…" @keyup.enter="performSearch(searchQuery)" />
                                <button class="btn btn-pacific" @click="performSearch(searchQuery)">Cerca</button>
                            </div>
                        </div>
                        <div style="flex: 0.8; min-width: 280px;">
                            <h3 class="download-panel-title">Download diretto</h3>
                            <div class="download-input-group">
                                <input class="download-input" v-model="downloadUrl" placeholder="utente/repo/file.gguf oppure URL" @keyup.enter="startDownload" />
                                <button class="btn btn-secondary" @click="startDownload">Scarica</button>
                            </div>
                        </div>
                    </div>

                    <div v-if="searchResults.length === 0 && !isSearching" class="empty-panel">
                        Nessun risultato. Cerca qui sopra per trovare altri modelli.
                    </div>

                    <div v-else class="models-grid">
                        <div v-for="r in searchResults" :key="r.repo_id" class="model-card">
                            <div class="model-card-header">
                                <div class="model-card-title" style="font-size: 13.5px;">{{ r.repo_id }}</div>
                                <div class="model-badge info">{{ r.downloads.toLocaleString() }} dl</div>
                            </div>
                            <div class="model-card-desc">{{ r.description }}</div>

                            <!-- File Selection Dropdown -->
                            <div class="form-group">
                                <label class="form-label" style="font-size: 11.5px;">File GGUF da scaricare</label>
                                <select v-model="r.selected_file" class="download-input" style="font-family: var(--font-mono); font-size: 11.5px;">
                                    <option v-for="f in r.gguf_files" :key="f.filename" :value="f.filename">
                                        {{ f.filename }} ({{ f.size_gb ? f.size_gb + ' GB' : 'dimensione ignota' }})
                                    </option>
                                </select>
                            </div>

                            <!-- Projector Selection -->
                            <div class="form-group" v-if="r.mmproj_files && r.mmproj_files.length > 0" style="background: rgba(var(--pacific-500-rgb), 0.1); padding: 12px; border-radius: var(--radius-md); border: 1px solid rgba(var(--pacific-500-rgb), 0.2);">
                                <label class="form-label" style="font-size: 11.5px; color: var(--pacific-500); display: flex; align-items: center; gap: 4px;">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                    Vision Support Detected
                                </label>
                                <div style="display: flex; align-items: center; gap: 8px; margin-top: 4px;">
                                    <input type="checkbox" v-model="r.download_mmproj" :id="'chk-'+r.repo_id.replace('/', '-')" />
                                    <label :for="'chk-'+r.repo_id.replace('/', '-')" style="font-size: 11.5px; cursor: pointer; color: var(--text-200);">Scarica il file projector associato per il riconoscimento immagini</label>
                                </div>
                                <select v-if="r.download_mmproj && r.mmproj_files.length > 1" v-model="r.selected_mmproj" class="download-input" style="font-family: var(--font-mono); font-size: 11.5px; margin-top: 8px;">
                                    <option v-for="f in r.mmproj_files" :key="f.filename" :value="f.filename">
                                        {{ f.filename }} ({{ f.size_gb ? f.size_gb + ' GB' : 'dimensione ignota' }})
                                    </option>
                                </select>
                            </div>

                            <div class="model-card-actions">
                                <button class="btn btn-pacific btn-block" @click="downloadSearchModel(r.repo_id, r.selected_file, r.download_mmproj ? (r.selected_mmproj || r.mmproj_files[0].filename) : null)">
                                    Scarica file selezionati
                                </button>
                            </div>
                        </div>
                    </div>

                    <div v-if="searchResults.length > 0 && hasNextPage" style="text-align: center; margin-top: 24px;">
                        <button class="btn btn-secondary" @click="loadMore" :disabled="isSearching">
                            <span v-if="isSearching" class="spinner spinner-sm"></span>
                            Carica altri risultati
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `,
    setup() {
        const ctrl = useModelsController();
        const { computed } = Vue;
        const hasInstalledChatModels = computed(() => {
            return (ctrl.chatModels.value || []).some(m => m.installed);
        });
        // Adapt the card's object-payload events to the controller's explicit
        // argument signatures, forwarding the model kind so the chat and
        // embedding defaults stay separate.
        const onSetDefault = (m) => ctrl.setDefaultModel(m.id, m.kind || 'chat');
        const onDownload = (m) => ctrl.downloadPreset(m.repo_id, m.name);
        return { ...ctrl, onSetDefault, onDownload, hasInstalledChatModels };
    }
};
