import { useModelsController } from '../controllers/models_controller.js';

export default {
    template: `
        <div style="display: flex; flex-direction: column; height: 100%; position: relative;">
            <!-- Loading Overlay -->
            <div v-if="isRestarting" class="loading-overlay">
                <div class="spinner"></div>
                <div>Riavvio del server dei modelli in corso...</div>
            </div>
            <div class="view-header">
                <h1 class="view-title">Gestione Modelli GGUF</h1>
            </div>
            <div class="view-body" style="padding: 30px;">
                <h3 class="mcp-section-header">Catalogo Modelli</h3>
                <div class="models-grid">
                    <div v-for="m in models" :key="m.id" class="model-card" :class="{ active: m.active, downloading: m.downloading }">
                        <div class="model-card-header">
                            <div class="model-card-title">{{ m.preset_id || m.name }}</div>
                            <div v-if="m.active" class="model-badge success">Attivo</div>
                            <div v-else-if="m.installed" class="model-badge secondary">Installato</div>
                            <div v-else-if="m.downloading" class="model-badge warning">Scaricamento...</div>
                            <div v-else class="model-badge info">Disponibile</div>
                        </div>

                        <!-- Technical filename -->
                        <div v-if="m.preset_id" style="font-size: 11px; color: var(--text-muted); font-family: monospace; margin-top: -6px; word-break: break-all;">
                            {{ m.name }}
                        </div>

                        <div class="model-card-desc">{{ m.description }}</div>
                        
                        <div class="model-card-meta">
                            <span>Dimensioni: {{ m.size }}</span>
                            <span>Quantizzazione: {{ m.quant }}</span>
                        </div>

                        <!-- Progress Bar for active downloads -->
                        <div v-if="m.downloading" style="margin-top: 8px;">
                            <div style="display: flex; justify-content: space-between; font-size: 11px; margin-bottom: 5px; color: var(--text-muted);">
                                <span>Avanzamento download</span>
                                <strong>{{ m.progress }}%</strong>
                            </div>
                            <div class="progress-bar-container">
                                <div class="progress-bar-fill" :style="{ width: m.progress + '%' }"></div>
                            </div>
                        </div>

                        <!-- Action Buttons -->
                        <div class="model-card-actions" style="margin-top: auto; padding-top: 15px;">
                            <!-- Case 1: Installed -->
                            <template v-if="m.installed">
                                <button v-if="!m.active" class="btn btn-secondary" @click="setDefaultModel(m.id)">Imposta default</button>
                                <button class="btn btn-danger" @click="deleteModel(m.id)">Elimina</button>
                            </template>
                            <!-- Case 2: Downloading -->
                            <template v-else-if="m.downloading">
                                <button class="btn btn-secondary" disabled style="opacity: 0.6; cursor: not-allowed; width: 100%;">Scaricamento in corso...</button>
                            </template>
                            <!-- Case 3: Available to download -->
                            <template v-else>
                                <button class="btn btn-pacific" @click="downloadPreset(m.repo_id, m.name)" style="width: 100%;">Scarica Modello</button>
                            </template>
                        </div>
                    </div>
                </div>
                <div style="height: 20px;"></div>

                <!-- Hugging Face Search Browser Results -->
                <h3 class="mcp-section-header" style="margin-top: 40px; display: flex; align-items: center; gap: 12px;">
                    Esplora Modelli su Hugging Face
                    <span v-if="isSearching" class="spinner" style="width: 16px; height: 16px; border-width: 2.5px; border-top-color: var(--amber-glow); display: inline-block;"></span>
                </h3>
                <!-- Download & Search Panel -->
                <div class="download-panel" style="display: flex; gap: 24px; flex-wrap: wrap; margin-bottom: 30px;">
                    <div style="flex: 1.2; min-width: 320px;">
                        <h3 class="download-panel-title">Cerca Modelli su Hugging Face</h3>
                        <div class="download-input-group">
                            <input class="download-input" v-model="searchQuery" placeholder="Cerca repository (es. llama, deepseek, gemma)..." @keyup.enter="performSearch(searchQuery)" />
                            <button class="btn btn-pacific" @click="performSearch(searchQuery)">Cerca</button>
                        </div>
                    </div>
                    <div style="flex: 0.8; min-width: 280px;">
                        <h3 class="download-panel-title">Download Personalizzato (URL/Path)</h3>
                        <div class="download-input-group">
                            <input class="download-input" v-model="downloadUrl" placeholder="utente/repo/nomefile.gguf o URL resolve" @keyup.enter="startDownload" />
                            <button class="btn btn-secondary" @click="startDownload">Scarica</button>
                        </div>
                    </div>
                </div>
                <div v-if="searchResults.length === 0 && !isSearching" style="padding: 30px; text-align: center; color: var(--text-muted); background: var(--bg-hover); border-radius: 8px; border: 1px dashed var(--border-color);">
                    Nessun risultato caricato. Inserisci una ricerca sopra per trovare altri modelli.
                </div>
                
                <div v-else class="models-grid">
                    <div v-for="r in searchResults" :key="r.repo_id" class="model-card">
                        <div class="model-card-header">
                            <div class="model-card-title" style="word-break: break-all; font-size: 14px;">{{ r.repo_id }}</div>
                            <div class="model-badge info" style="font-size: 11px;">{{ r.downloads.toLocaleString() }} download</div>
                        </div>
                        <div class="model-card-desc" style="font-size: 12px; margin-top: 8px;">{{ r.description }}</div>
                        
                        <!-- File Selection Dropdown -->
                        <div style="margin-top: 15px; margin-bottom: 10px;">
                            <label style="font-size: 11.5px; color: var(--text-muted); display: block; margin-bottom: 6px; font-weight: 500;">
                                Seleziona file GGUF da scaricare:
                            </label>
                            <select v-model="r.selected_file" class="download-input" style="width: 100%; padding: 8px; font-size: 12.5px; background: var(--bg-color); border: 1px solid var(--border-color); border-radius: 6px; color: var(--text-color);">
                                <option v-for="f in r.files" :key="f.filename" :value="f.filename">
                                    {{ f.filename }} ({{ f.size_gb ? f.size_gb + ' GB' : 'Dimensioni sconosciute' }})
                                </option>
                            </select>
                        </div>
                        
                        <div class="model-card-actions" style="margin-top: auto; padding-top: 15px;">
                            <button class="btn btn-pacific" @click="downloadSearchModel(r.repo_id, r.selected_file)" style="width: 100%;">
                                Scarica file selezionato
                            </button>
                        </div>
                    </div>
                </div>
                
                <div v-if="searchResults.length > 0 && hasNextPage" style="text-align: center; margin-top: 30px;">
                    <button class="btn btn-pacific" @click="loadMore" :disabled="isSearching" style="display: inline-flex; align-items: center; gap: 8px;">
                        <span v-if="isSearching" class="spinner" style="width: 14px; height: 14px; border-width: 2px; border-top-color: var(--amber-glow); display: inline-block; vertical-align: middle;"></span>
                        Carica Altri Risultati
                    </button>
                </div>
            </div>
        </div>
    `,
    setup() {
        return useModelsController();
    }
};
